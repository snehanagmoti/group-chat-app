package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"math"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/http/httputil"
	"net/url"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// ─── Backend ──────────────────────────────────────────────────────────────────

type Backend struct {
	URL        *url.URL
	Alive      atomic.Bool
	InFlight   atomic.Int64
	Overloaded atomic.Bool            // true when EWMA (real request latency only) > thresholdMs
	proxy      *httputil.ReverseProxy // created once at startup; reused for every request

	ewmaMu sync.Mutex
	ewmaMs float64 // exponential weighted moving average latency in ms — driven ONLY by real completed requests

	// errorCooldownUntil holds a UnixNano deadline. While now < deadline, the backend
	// is treated as overloaded because of a recent error/timeout — kept entirely
	// separate from ewmaMs so a single slow/failed request can't poison the latency
	// signal used for real request routing (see isOverloaded/tripErrorCooldown).
	errorCooldownUntil atomic.Int64
}

// tripErrorCooldown marks the backend as overloaded for a fixed, deterministic
// window following an error/timeout. Unlike the EWMA, this decays purely by wall
// clock — it doesn't require the backend to go idle and doesn't compound with
// real traffic latency, so one bad response can't cause a runaway spiral.
func (b *Backend) tripErrorCooldown(d time.Duration) {
	b.errorCooldownUntil.Store(time.Now().Add(d).UnixNano())
}

// isOverloaded is the single source of truth bestBackend() and reporting should use:
// true if either real-traffic EWMA is over threshold, OR we're still inside the
// post-error cooldown window.
func (b *Backend) isOverloaded() bool {
	if time.Now().UnixNano() < b.errorCooldownUntil.Load() {
		return true
	}
	return b.Overloaded.Load()
}

func (b *Backend) recordLatency(d time.Duration, alpha, thresholdMs float64) {
	b.ewmaMu.Lock()
	defer b.ewmaMu.Unlock()
	ms := float64(d.Milliseconds())
	if b.ewmaMs == 0 {
		b.ewmaMs = ms
	} else {
		b.ewmaMs = alpha*ms + (1-alpha)*b.ewmaMs
	}
	b.Overloaded.Store(b.ewmaMs > thresholdMs)
}

func (b *Backend) score() float64 {
	b.ewmaMu.Lock()
	ewma := b.ewmaMs
	b.ewmaMu.Unlock()
	if ewma == 0 {
		ewma = 1
	}
	return float64(b.InFlight.Load()+1) * ewma
}

func (b *Backend) getEWMA() float64 {
	b.ewmaMu.Lock()
	defer b.ewmaMu.Unlock()
	return b.ewmaMs
}

// decayIdle ages the EWMA toward zero as if a fast (0ms) sample had arrived.
// Used by the health-check loop so a healthy, idle backend's latency signal
// recovers over time WITHOUT mixing raw health-probe latency (typically 1-5ms)
// into the same average as real request latency — the two are not comparable
// and blending them previously masked genuine overload.
func (b *Backend) decayIdle(alpha, thresholdMs float64) {
	b.ewmaMu.Lock()
	defer b.ewmaMu.Unlock()
	if b.ewmaMs == 0 {
		return
	}
	b.ewmaMs = (1 - alpha) * b.ewmaMs
	b.Overloaded.Store(b.ewmaMs > thresholdMs)
}

// ─── Metrics ──────────────────────────────────────────────────────────────────

const latencyBufferCap = 2000 // bounds memory + sort cost regardless of request volume

// latencyRing is a small fixed-capacity ring buffer of durations, guarded by
// its own mutex. Used for several independent latency signals below so each
// can be sampled/percentiled without one polluting another.
type latencyRing struct {
	mu     sync.Mutex
	values []time.Duration
	idx    int
	filled bool
}

func (r *latencyRing) record(d time.Duration) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.values == nil {
		r.values = make([]time.Duration, latencyBufferCap)
	}
	r.values[r.idx] = d
	r.idx = (r.idx + 1) % latencyBufferCap
	if r.idx == 0 {
		r.filled = true
	}
}

// snapshot returns a copy of the samples currently in the ring buffer.
func (r *latencyRing) snapshot() []time.Duration {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := r.idx
	if r.filled {
		n = latencyBufferCap
	}
	cp := make([]time.Duration, n)
	copy(cp, r.values[:n])
	return cp
}

type Metrics struct {
	Total         atomic.Uint64
	Success       atomic.Uint64
	Failed        atomic.Uint64
	BackendErrors atomic.Uint64

	// Latencies is the total, client-observed request latency: connection
	// acquisition (queueing on the pool) + backend processing time. This is
	// what a load-test tool sees, so it stays as the headline p50/p95/p99.
	Latencies latencyRing

	// QueueWait is time spent waiting to acquire a connection from the
	// shared transport's pool (MaxConnsPerHost) before the request could
	// even be sent to the backend. Tracked separately so it can be told
	// apart from genuine backend slowness — see ServeHTTP.
	QueueWait latencyRing

	// BackendOnly is Latencies minus QueueWait: the portion of each request
	// actually spent waiting on the backend. This — not the total — is what
	// feeds each Backend's EWMA, so pool contention can't be misread as the
	// backend itself being slow.
	BackendOnly latencyRing
}

func (m *Metrics) recordLatencySample(d time.Duration)     { m.Latencies.record(d) }
func (m *Metrics) recordQueueWaitSample(d time.Duration)   { m.QueueWait.record(d) }
func (m *Metrics) recordBackendOnlySample(d time.Duration) { m.BackendOnly.record(d) }

func (m *Metrics) snapshotLatencies() []time.Duration   { return m.Latencies.snapshot() }
func (m *Metrics) snapshotQueueWait() []time.Duration   { return m.QueueWait.snapshot() }
func (m *Metrics) snapshotBackendOnly() []time.Duration { return m.BackendOnly.snapshot() }

// ─── LoadBalancer ─────────────────────────────────────────────────────────────

type LoadBalancer struct {
	backends      []*Backend
	metrics       Metrics
	timeout       time.Duration
	thresholdMs   float64
	ewmaAlpha     float64
	errorCooldown time.Duration // how long a backend is treated as overloaded after an error/timeout
}

func (lb *LoadBalancer) bestBackend() *Backend {
	var best *Backend
	bestScore := math.MaxFloat64

	// Pass 1: prefer non-overloaded, alive backends (or idle overloaded ones to prevent starvation)
	for _, b := range lb.backends {
		if !b.Alive.Load() || (b.isOverloaded() && b.InFlight.Load() > 0) {
			continue
		}
		if s := b.score(); s < bestScore {
			bestScore = s
			best = b
		}
	}
	if best != nil {
		return best
	}

	// Pass 2: all healthy backends are overloaded — pick least in-flight alive one
	var minFlight int64 = math.MaxInt64
	for _, b := range lb.backends {
		if !b.Alive.Load() {
			continue
		}
		if f := b.InFlight.Load(); f < minFlight {
			minFlight = f
			best = b
		}
	}
	return best // nil only if ALL backends are dead
}

func (lb *LoadBalancer) healthLoop(interval time.Duration) {
	// InsecureSkipVerify allows the health checker to reach backends
	// using self-signed TLS certificates (the Python messaging backend).
	client := &http.Client{
		Timeout: 2 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		},
	}
	for {
		// Fire all probes concurrently. Previously each backend was probed
		// sequentially in this single goroutine with up to a 2s client
		// timeout apiece — one slow/unreachable backend could delay the
		// UNHEALTHY/HEALTHY detection of every backend behind it in the
		// list by seconds. A WaitGroup lets every probe run in parallel so
		// the tick's total duration is bounded by the slowest single probe,
		// not the sum of all of them.
		var wg sync.WaitGroup
		for _, b := range lb.backends {
			wg.Add(1)
			go func(b *Backend) {
				defer wg.Done()

				probeStart := time.Now()
				resp, err := client.Get(b.URL.String() + "/health")
				_ = time.Since(probeStart) // probe latency is a liveness signal only, not a request-latency sample
				if err != nil || resp.StatusCode >= 500 {
					if b.Alive.Load() {
						log.Printf("[health] backend %s -> UNHEALTHY", b.URL)
					}
					b.Alive.Store(false)
				} else {
					if !b.Alive.Load() {
						log.Printf("[health] backend %s -> HEALTHY", b.URL)
					}
					b.Alive.Store(true)
					// Only decay the EWMA when the backend is genuinely idle — if it has
					// in-flight requests, real request latency should drive the signal,
					// not a fast /health ping that bypasses whatever is slowing real traffic.
					if b.InFlight.Load() == 0 {
						b.decayIdle(lb.ewmaAlpha, lb.thresholdMs)
					}
				}
				if resp != nil {
					resp.Body.Close()
				}
			}(b)
		}
		wg.Wait()
		time.Sleep(interval)
	}
}

// ─── Monitoring Handlers ──────────────────────────────

// healthHandler: GET /lb/health
func (lb *LoadBalancer) healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// statusHandler: GET /lb/status
func (lb *LoadBalancer) statusHandler(w http.ResponseWriter, r *http.Request) {
	type entry struct {
		URL            string  `json:"url"`
		Alive          bool    `json:"alive"`
		InFlight       int64   `json:"in_flight"`
		Overloaded     bool    `json:"overloaded"`      // combined: EWMA-over-threshold OR in error cooldown
		EWMAOverloaded bool    `json:"ewma_overloaded"` // real-traffic latency signal only
		ErrorCooldown  bool    `json:"error_cooldown"`  // true if still inside post-error cooldown window
		EWMAMs         float64 `json:"ewma_ms"`
	}
	var list []entry
	for _, b := range lb.backends {
		list = append(list, entry{
			URL:            b.URL.String(),
			Alive:          b.Alive.Load(),
			InFlight:       b.InFlight.Load(),
			Overloaded:     b.isOverloaded(),
			EWMAOverloaded: b.Overloaded.Load(),
			ErrorCooldown:  time.Now().UnixNano() < b.errorCooldownUntil.Load(),
			EWMAMs:         b.getEWMA(),
		})
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"backends": list})
}

// metricsHandler: GET /lb/metrics
func (lb *LoadBalancer) metricsHandler(w http.ResponseWriter, r *http.Request) {
	total := lb.metrics.snapshotLatencies()
	queueWait := lb.metrics.snapshotQueueWait()
	backendOnly := lb.metrics.snapshotBackendOnly()

	sort.Slice(total, func(i, j int) bool { return total[i] < total[j] })
	sort.Slice(queueWait, func(i, j int) bool { return queueWait[i] < queueWait[j] })
	sort.Slice(backendOnly, func(i, j int) bool { return backendOnly[i] < backendOnly[j] })

	overloadedCount := 0
	for _, b := range lb.backends {
		if b.isOverloaded() {
			overloadedCount++
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"total":          lb.metrics.Total.Load(),
		"success":        lb.metrics.Success.Load(),
		"failed":         lb.metrics.Failed.Load(),
		"backend_errors": lb.metrics.BackendErrors.Load(),
		"p50_ms":         percentile(total, 50).Milliseconds(),
		"p95_ms":         percentile(total, 95).Milliseconds(),
		"p99_ms":         percentile(total, 99).Milliseconds(),
		// Breakdown of the total above: how much was spent waiting on a free
		// pooled connection (pool contention) vs. actually waiting on the
		// backend. If queue_wait tracks your load generator's concurrency
		// rather than backend p50_ms, the bottleneck is MaxConnsPerHost /
		// MaxIdleConnsPerHost, not the backends themselves.
		"queue_wait_p50_ms":   percentile(queueWait, 50).Milliseconds(),
		"queue_wait_p95_ms":   percentile(queueWait, 95).Milliseconds(),
		"queue_wait_p99_ms":   percentile(queueWait, 99).Milliseconds(),
		"backend_only_p50_ms": percentile(backendOnly, 50).Milliseconds(),
		"backend_only_p95_ms": percentile(backendOnly, 95).Milliseconds(),
		"backend_only_p99_ms": percentile(backendOnly, 99).Milliseconds(),
		"threshold_ms":        lb.thresholdMs,
		"ewma_alpha":          lb.ewmaAlpha,
		"overloaded_backends": overloadedCount,
	})
}

// ─── Forward Handler ──────────────────────────

func (lb *LoadBalancer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	lb.metrics.Total.Add(1)
	start := time.Now()

	b := lb.bestBackend()
	if b == nil {
		lb.metrics.Failed.Add(1)
		http.Error(w, "no healthy backend available", http.StatusServiceUnavailable)
		return
	}

	b.InFlight.Add(1)
	defer b.InFlight.Add(-1)

	// Apply per-request timeout via context
	ctx, cancel := context.WithTimeout(r.Context(), lb.timeout)
	defer cancel()

	// Instrument the round trip so we can tell apart two very different
	// costs that both used to land in one "elapsed" number:
	//   - queue wait: time blocked waiting for a free connection out of the
	//     shared transport's pool (MaxConnsPerHost), or the time spent
	//     dialing a brand-new one. This is pool contention, not the backend
	//     being slow.
	//   - backend time: everything after a connection was actually in hand
	//     — i.e. the backend really processing the request.
	// GetConn fires right before the transport tries to obtain a
	// connection; GotConn fires once it has one (reused or freshly dialed).
	var getConnAt, gotConnAt time.Time
	trace := &httptrace.ClientTrace{
		GetConn: func(hostPort string) { getConnAt = time.Now() },
		GotConn: func(info httptrace.GotConnInfo) { gotConnAt = time.Now() },
	}
	ctx = httptrace.WithClientTrace(ctx, trace)
	r = r.WithContext(ctx)

	// Delegate to the pre-created, connection-pooling reverse proxy
	b.proxy.ServeHTTP(w, r)

	if r.Context().Err() == nil && b.Alive.Load() {
		lb.metrics.Success.Add(1)
		elapsed := time.Since(start)

		var queueWait time.Duration
		if !getConnAt.IsZero() && !gotConnAt.IsZero() {
			queueWait = gotConnAt.Sub(getConnAt)
		}
		backendOnly := elapsed - queueWait
		if backendOnly < 0 { // defensive; shouldn't happen but never let EWMA go negative
			backendOnly = elapsed
		}

		// EWMA (and therefore bestBackend()'s routing decisions) is driven
		// ONLY by backend-only time now, so time spent queued for a
		// connection out of the pool can't be misattributed to the backend
		// being slow and needlessly steer traffic away from a backend that
		// is actually healthy.
		b.recordLatency(backendOnly, lb.ewmaAlpha, lb.thresholdMs)

		// Total client-observed latency stays the headline metric (it's what
		// a load-test tool actually measures), with the two components also
		// tracked separately so /lb/metrics can show whether latency is
		// coming from backend processing or from pool queueing.
		lb.metrics.recordLatencySample(elapsed)
		lb.metrics.recordQueueWaitSample(queueWait)
		lb.metrics.recordBackendOnlySample(backendOnly)
	}
}

// ─── Helper ───────────────────────────────────────────────────────────────────

func percentile(sorted []time.Duration, p float64) time.Duration {
	if len(sorted) == 0 {
		return 0
	}
	idx := int(float64(len(sorted)-1) * p / 100.0)
	return sorted[idx]
}

// ─── main ─────────────────────────────────────────────────────────────────────

func main() {
	addr := flag.String("addr", ":8080",
		"address the load balancer listens on")

	backendsFlag := flag.String("backends", "http://localhost:8081",
		"comma-separated list of backend URLs")

	healthInterval := flag.Duration("health-interval", 1*time.Second,
		"how often to probe backend /health")

	backendTimeout := flag.Duration("backend-timeout", 2500*time.Millisecond,
		"backend request timeout")

	thresholdMs := flag.Float64("threshold-ms", 300,
		"EWMA ms above which backend is overloaded")

	ewmaAlpha := flag.Float64("ewma-alpha", 0.2,
		"smoothing factor: higher = faster reaction")

	errorCooldown := flag.Duration("error-cooldown", 3*time.Second,
		"how long a backend is treated as overloaded after an error/timeout, independent of latency EWMA")

	maxConnsPerHost := flag.Int("max-conns-per-host", 500,
		"max concurrent connections per backend; set this >= (target concurrent virtual users / number of backends) or requests queue for a pooled connection and that queueing shows up as latency (see /lb/metrics queue_wait_*)")

	maxIdleConnsPerHost := flag.Int("max-idle-conns-per-host", 250,
		"idle keep-alive connections kept warm per backend")

	flag.Parse()

	// Build backend list with pre-created connection-pooling proxies
	//
	// One shared TLS transport per process:
	//   - MaxIdleConnsPerHost  → keeps TCP connections warm (avoids TCP handshake on every request)
	//   - IdleConnTimeout      → evicts stale keep-alive connections after 90 s
	//   - InsecureSkipVerify   → allows self-signed certs from the Python backend
	//
	// MaxConnsPerHost is the hard cap on connections in flight to ANY ONE
	// backend. Once that many requests to a backend are outstanding, the
	// transport queues further requests until a connection frees up —
	// that queueing time is measured separately in ServeHTTP and reported
	// as queue_wait_* in /lb/metrics precisely so it isn't mistaken for the
	// backend being slow. Size this flag for your actual test concurrency:
	// e.g. 500 virtual users spread over 2 backends needs >=250 here per
	// backend, not 500 total capacity shared unevenly.
	sharedTransport := &http.Transport{
		MaxIdleConns:        1000,
		MaxIdleConnsPerHost: *maxIdleConnsPerHost,
		MaxConnsPerHost:     *maxConnsPerHost,
		IdleConnTimeout:     90 * time.Second,
		TLSClientConfig:     &tls.Config{InsecureSkipVerify: true},
		DialContext: (&net.Dialer{
			Timeout:   2 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		// NOTE: deliberately no ResponseHeaderTimeout here. It used to be set to the
		// same duration as the per-request context timeout below, so the two raced —
		// whichever fired first produced a differently-shaped error. When
		// ResponseHeaderTimeout won the race it returned Go's internal
		// "timeout awaiting response headers" error, which errors.Is() does NOT
		// recognize as context.DeadlineExceeded, so the ErrorHandler misclassified
		// a merely-slow-but-alive backend as dead. The per-request context deadline
		// (lb.timeout) is now the single source of truth for request timeouts.
	}

	lb := &LoadBalancer{
		timeout:       *backendTimeout,
		thresholdMs:   *thresholdMs,
		ewmaAlpha:     *ewmaAlpha,
		errorCooldown: *errorCooldown,
	}
	for _, raw := range strings.Split(*backendsFlag, ",") {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}
		u, err := url.Parse(raw)
		if err != nil {
			log.Fatalf("invalid backend URL %q: %v", raw, err)
		}
		b := &Backend{URL: u}

		// Create the reverse proxy once; bind error handler via closure over b + lb
		p := httputil.NewSingleHostReverseProxy(u)
		p.Transport = sharedTransport
		p.ErrorHandler = func(rw http.ResponseWriter, req *http.Request, err error) {
			isTimeout := errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
			if !isTimeout {
				// Catches timeouts that don't wrap a context error (e.g. transport-level
				// timeouts), so any kind of "too slow" is treated as busy, not dead.
				var netErr net.Error
				if errors.As(err, &netErr) && netErr.Timeout() {
					isTimeout = true
				}
			}

			// Under high load, an overloaded Gunicorn sends TCP RST (connection
			// refused) or closes the connection mid-stream (EOF/unexpected EOF).
			// These are transient overload signals — not a dead server — so treat
			// them the same as timeouts: error cooldown rather than marking dead.
			// Only truly dead backends (refused to TLS-handshake, bad cert, etc.)
			// should be permanently removed from rotation.
			errStr := err.Error()
			isTransient := isTimeout ||
				strings.Contains(errStr, "connection refused") ||
				strings.Contains(errStr, "EOF") ||
				strings.Contains(errStr, "reset by peer") ||
				strings.Contains(errStr, "broken pipe")

			if !isTransient {
				b.Alive.Store(false)
			}
			// IMPORTANT: do NOT call b.recordLatency(lb.timeout, ...) here. Feeding the
			// full configured timeout into the real-request EWMA as a fake "sample"
			// used to cause a single error to spike the average toward lb.timeout,
			// which (a) could only be brought back down by decayIdle() during health
			// checks, which only runs when InFlight==0 — impossible on a backend under
			// sustained load — and (b) once Overloaded flipped true, bestBackend()
			// routed all new traffic to the remaining backends, overloading them too.
			// One slow/failed request could cascade into taking every backend out of
			// rotation. tripErrorCooldown gives the same "back off this backend"
			// behavior but decays deterministically by wall-clock time, independent of
			// traffic, and never touches the EWMA that real request routing relies on.
			b.tripErrorCooldown(lb.errorCooldown)
			lb.metrics.BackendErrors.Add(1)
			lb.metrics.Failed.Add(1)
			if isTimeout {
				http.Error(rw, "backend busy/timeout", http.StatusGatewayTimeout)
			} else {
				http.Error(rw, "backend unavailable", http.StatusBadGateway)
			}
		}
		b.proxy = p
		b.Alive.Store(true)

		lb.backends = append(lb.backends, b)
		log.Printf("[LB] registered backend: %s", u)
	}

	if len(lb.backends) == 0 {
		log.Fatal("no backends configured")
	}

	// Start health check goroutine
	go lb.healthLoop(*healthInterval)

	mux := http.NewServeMux()
	mux.HandleFunc("/lb/health", lb.healthHandler)
	mux.HandleFunc("/lb/status", lb.statusHandler)
	mux.HandleFunc("/lb/metrics", lb.metricsHandler)
	mux.Handle("/", lb)

	fmt.Printf("\nLoad Balancer listening on %s\n", *addr)
	fmt.Printf("Backends   : %d\n", len(lb.backends))
	fmt.Printf("Threshold  : %.1f ms\n", *thresholdMs)
	fmt.Printf("EWMA Alpha : %.2f\n", *ewmaAlpha)
	fmt.Printf("Health     : every %s\n", *healthInterval)
	fmt.Printf("Timeout    : %s\n", *backendTimeout)
	fmt.Printf("Err Cooldn : %s\n", *errorCooldown)
	fmt.Printf("Max Conns/Host      : %d\n", *maxConnsPerHost)
	fmt.Printf("Max Idle Conns/Host : %d\n\n", *maxIdleConnsPerHost)
	fmt.Printf("  /lb/health   — LB liveness\n")
	fmt.Printf("  /lb/status   — backend states\n")
	fmt.Printf("  /lb/metrics  — counters + latency\n")
	fmt.Printf("  /            — forward to backends\n\n")

	log.Fatal(http.ListenAndServe(*addr, mux))
}
