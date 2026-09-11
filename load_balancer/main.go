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
	Overloaded atomic.Bool            // true when EWMA > thresholdMs
	proxy      *httputil.ReverseProxy // created once at startup; reused for every request

	ewmaMu sync.Mutex
	ewmaMs float64 // exponential weighted moving average latency in ms
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

type Metrics struct {
	Total         atomic.Uint64
	Success       atomic.Uint64
	Failed        atomic.Uint64
	BackendErrors atomic.Uint64

	LatencyMu sync.Mutex
	Latencies []time.Duration // fixed-capacity ring buffer, see recordLatencySample
	latIdx    int
	latFilled bool
}

const latencyBufferCap = 2000 // bounds memory + sort cost regardless of request volume

func (m *Metrics) recordLatencySample(d time.Duration) {
	m.LatencyMu.Lock()
	defer m.LatencyMu.Unlock()
	if m.Latencies == nil {
		m.Latencies = make([]time.Duration, latencyBufferCap)
	}
	m.Latencies[m.latIdx] = d
	m.latIdx = (m.latIdx + 1) % latencyBufferCap
	if m.latIdx == 0 {
		m.latFilled = true
	}
}

// snapshotLatencies returns a copy of the samples currently in the ring buffer.
func (m *Metrics) snapshotLatencies() []time.Duration {
	m.LatencyMu.Lock()
	defer m.LatencyMu.Unlock()
	n := m.latIdx
	if m.latFilled {
		n = latencyBufferCap
	}
	cp := make([]time.Duration, n)
	copy(cp, m.Latencies[:n])
	return cp
}

// ─── LoadBalancer ─────────────────────────────────────────────────────────────

type LoadBalancer struct {
	backends    []*Backend
	metrics     Metrics
	timeout     time.Duration
	thresholdMs float64
	ewmaAlpha   float64
}

func (lb *LoadBalancer) bestBackend() *Backend {
	var best *Backend
	bestScore := math.MaxFloat64

	// Pass 1: prefer non-overloaded, alive backends (or idle overloaded ones to prevent starvation)
	for _, b := range lb.backends {
		if !b.Alive.Load() || (b.Overloaded.Load() && b.InFlight.Load() > 0) {
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
		for _, b := range lb.backends {
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
		}
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
		URL        string  `json:"url"`
		Alive      bool    `json:"alive"`
		InFlight   int64   `json:"in_flight"`
		Overloaded bool    `json:"overloaded"`
		EWMAMs     float64 `json:"ewma_ms"`
	}
	var list []entry
	for _, b := range lb.backends {
		list = append(list, entry{
			URL:        b.URL.String(),
			Alive:      b.Alive.Load(),
			InFlight:   b.InFlight.Load(),
			Overloaded: b.Overloaded.Load(),
			EWMAMs:     b.getEWMA(),
		})
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"backends": list})
}

// metricsHandler: GET /lb/metrics
func (lb *LoadBalancer) metricsHandler(w http.ResponseWriter, r *http.Request) {
	cp := lb.metrics.snapshotLatencies()

	sort.Slice(cp, func(i, j int) bool { return cp[i] < cp[j] })

	overloadedCount := 0
	for _, b := range lb.backends {
		if b.Overloaded.Load() {
			overloadedCount++
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"total":               lb.metrics.Total.Load(),
		"success":             lb.metrics.Success.Load(),
		"failed":              lb.metrics.Failed.Load(),
		"backend_errors":      lb.metrics.BackendErrors.Load(),
		"p50_ms":              percentile(cp, 50).Milliseconds(),
		"p95_ms":              percentile(cp, 95).Milliseconds(),
		"p99_ms":              percentile(cp, 99).Milliseconds(),
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
	r = r.WithContext(ctx)

	// Delegate to the pre-created, connection-pooling reverse proxy
	b.proxy.ServeHTTP(w, r)

	if r.Context().Err() == nil && b.Alive.Load() {
		lb.metrics.Success.Add(1)
		elapsed := time.Since(start)
		b.recordLatency(elapsed, lb.ewmaAlpha, lb.thresholdMs)
		lb.metrics.recordLatencySample(elapsed)
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

	flag.Parse()

	// Build backend list with pre-created connection-pooling proxies
	//
	// One shared TLS transport per process:
	//   - MaxIdleConnsPerHost  → keeps TCP connections warm (avoids TCP handshake on every request)
	//   - IdleConnTimeout      → evicts stale keep-alive connections after 90 s
	//   - InsecureSkipVerify   → allows self-signed certs from the Python backend
	sharedTransport := &http.Transport{
		MaxIdleConns:        1000,
		MaxIdleConnsPerHost: 250,
		MaxConnsPerHost:     500, // bound total (not just idle) conns per backend so a burst can't open unlimited sockets
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
		timeout:     *backendTimeout,
		thresholdMs: *thresholdMs,
		ewmaAlpha:   *ewmaAlpha,
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
			// Only mark dead if it's a real network connection failure, not high-load timeout
			if !isTimeout {
				b.Alive.Store(false)
			}
			b.Overloaded.Store(true)
			b.recordLatency(lb.timeout, lb.ewmaAlpha, lb.thresholdMs)
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
	fmt.Printf("Timeout    : %s\n\n", *backendTimeout)
	fmt.Printf("  /lb/health   — LB liveness\n")
	fmt.Printf("  /lb/status   — backend states\n")
	fmt.Printf("  /lb/metrics  — counters + latency\n")
	fmt.Printf("  /            — forward to backends\n\n")

	log.Fatal(http.ListenAndServe(*addr, mux))
}
