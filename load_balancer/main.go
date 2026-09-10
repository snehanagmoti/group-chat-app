package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
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

	"github.com/google/uuid"
)

// Backend holds state for an individual backend server instance.
type Backend struct {
	URL          *url.URL
	ReverseProxy *httputil.ReverseProxy
	InFlight     atomic.Int64

	// Health state machine
	mu         sync.Mutex
	State      HealthState
	FailStreak int
	OkStreak   int

	// Performance metrics (CPU, Memory, Latency from /internal/health)
	// ActiveRequests is NOT stored here — sourced from InFlight at score time.
	Metrics BackendMetrics

	// cfg is a copy of the LB's scoring config so getScore() can compute live
	cfg ScoringConfig
}

// Metrics tracks runtime operational performance counters.
type Metrics struct {
	Total         atomic.Uint64
	Success       atomic.Uint64
	Failed        atomic.Uint64
	BackendErrors atomic.Uint64
	SwitchCount   atomic.Uint64 // number of times routing switched backends
	LatencyMu     sync.Mutex
	Latencies     []time.Duration
}

// LoadBalancer manages backend routing, health monitoring, metrics, and scoring.
type LoadBalancer struct {
	backends       []*Backend
	next           atomic.Uint64 // used only as fallback if all scores equal
	metrics        Metrics
	healthInterval time.Duration
	backendTimeout time.Duration
	httpClient     *http.Client
	cfg            ScoringConfig

	// Track current backend per client connection (best-effort sticky within a session)
	currentMu      sync.Mutex
	currentBackend *Backend
}

// NewLoadBalancer initializes the LoadBalancer with parsed backends and configurations.
func NewLoadBalancer(backendURLs []string, healthInterval, backendTimeout time.Duration, cfg ScoringConfig) *LoadBalancer {
	lb := &LoadBalancer{
		healthInterval: healthInterval,
		backendTimeout: backendTimeout,
		cfg:            cfg,
		httpClient: &http.Client{
			Timeout: 2 * time.Second,
		},
	}

	for _, rawURL := range backendURLs {
		rawURL = strings.TrimSpace(rawURL)
		if rawURL == "" {
			continue
		}
		u, err := url.Parse(rawURL)
		if err != nil {
			log.Fatalf("Invalid backend URL %q: %v", rawURL, err)
		}

		b := &Backend{
			URL:   u,
			State: StateHealthy, // assume healthy at startup
			cfg:   cfg,          // copy so getScore() is self-contained
		}

		// Create reverse proxy with customized transport and timeout settings
		proxy := httputil.NewSingleHostReverseProxy(u)

		// Configure custom transport for strict timeouts and WebSocket/HTTP connection pooling
		transport := &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			DialContext: (&net.Dialer{
				Timeout:   2 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			ForceAttemptHTTP2:     false,
			MaxIdleConns:          1000,
			MaxIdleConnsPerHost:   200,
			IdleConnTimeout:       90 * time.Second,
			TLSHandshakeTimeout:   5 * time.Second,
			ResponseHeaderTimeout: backendTimeout,
		}
		proxy.Transport = transport

		// Error handler for backend connection issues — triggers passive failure detection
		proxy.ErrorHandler = func(rw http.ResponseWriter, req *http.Request, err error) {
			b.recordFailure()
			lb.metrics.BackendErrors.Add(1)
			lb.metrics.Failed.Add(1)
			log.Printf("[LB WARN] Backend %s failure: %v", b.URL.String(), err)
			http.Error(rw, `{"error": "backend unavailable"}`, http.StatusBadGateway)
		}

		b.ReverseProxy = proxy
		lb.backends = append(lb.backends, b)
	}

	return lb
}

// ── Backend helpers ────────────────────────────────────────────────────────────

func (b *Backend) isRoutable() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	// Health state machine is the SOLE gate for routability.
	// Stale metrics are logged but do NOT block routing — a backend that
	// can't reach /internal/health is still routable (state machine decides via /health).
	// A dead backend will fail /health checks and be marked UNHEALTHY by recordFailure().
	return b.State.IsRoutable()
}

func (b *Backend) getScore() float64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	// Score is always computed with the live InFlight counter.
	// We recompute here (not cache) so the score never lags behind actual concurrency.
	inFlight := b.InFlight.Load()
	b.Metrics.mu.Lock()
	s := ComputeScore(b.Metrics, inFlight, b.cfg)
	b.Metrics.mu.Unlock()
	return s
}

func (b *Backend) recordFailure() HealthState {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.OkStreak = 0
	b.FailStreak++
	switch {
	case b.FailStreak >= failsToUnhealthy:
		if b.State != StateUnhealthy {
			log.Printf("[HEALTH] Backend %s → UNHEALTHY (fail streak: %d)", b.URL.Host, b.FailStreak)
		}
		b.State = StateUnhealthy
	case b.FailStreak >= 1 && b.State == StateHealthy:
		b.State = StateSuspect
		log.Printf("[HEALTH] Backend %s → SUSPECT (fail streak: %d)", b.URL.Host, b.FailStreak)
	}
	return b.State
}

func (b *Backend) recordSuccess() HealthState {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.FailStreak = 0
	b.OkStreak++
	switch b.State {
	case StateUnhealthy:
		if b.OkStreak >= successesToOK {
			b.State = StateRecovering
			log.Printf("[HEALTH] Backend %s → RECOVERING (ok streak: %d)", b.URL.Host, b.OkStreak)
		}
	case StateRecovering:
		if b.OkStreak >= successesToOK+1 {
			b.State = StateHealthy
			log.Printf("[HEALTH] Backend %s → HEALTHY", b.URL.Host)
		}
	case StateSuspect:
		b.State = StateHealthy
		log.Printf("[HEALTH] Backend %s → HEALTHY (recovered from suspect)", b.URL.Host)
	}
	return b.State
}

// ── nextBackend: score-based selection with hysteresis ────────────────────────

// nextBackend selects the best backend using the weighted LoadScore.
// It prefers the current backend unless a candidate is meaningfully better
// (hysteresis) or the current backend is overloaded / unhealthy.
func (lb *LoadBalancer) nextBackend() *Backend {
	lb.currentMu.Lock()
	current := lb.currentBackend
	lb.currentMu.Unlock()

	// Collect all routable backends
	var candidates []*Backend
	for _, b := range lb.backends {
		if b.isRoutable() {
			candidates = append(candidates, b)
		}
	}
	if len(candidates) == 0 {
		return nil
	}

	// Find argmin(score) among candidates
	best := candidates[0]
	for _, b := range candidates[1:] {
		if b.getScore() < best.getScore() {
			best = b
		}
	}

	// Decision logic: hysteresis to prevent flapping
	if current == nil || !current.isRoutable() {
		// No valid current → pick best
		lb.currentMu.Lock()
		lb.currentBackend = best
		lb.currentMu.Unlock()
		return best
	}

	currentScore := current.getScore()
	bestScore := best.getScore()

	var chosen *Backend
	switch {
	case currentScore > lb.cfg.OverloadThreshold:
		// Current is overloaded — must switch
		chosen = best
		log.Printf("[ROUTE] Switch: %s(%.2f) OVERLOADED → %s(%.2f)",
			current.URL.Host, currentScore, best.URL.Host, bestScore)
		lb.metrics.SwitchCount.Add(1)
	case bestScore+lb.cfg.Hysteresis < currentScore:
		// Best is meaningfully better → switch
		chosen = best
		log.Printf("[ROUTE] Switch: %s(%.2f) → %s(%.2f) [hysteresis gap: %.2f]",
			current.URL.Host, currentScore, best.URL.Host, bestScore,
			currentScore-bestScore)
		lb.metrics.SwitchCount.Add(1)
	default:
		// Stick with current — avoid oscillation
		chosen = current
	}

	lb.currentMu.Lock()
	lb.currentBackend = chosen
	lb.currentMu.Unlock()
	return chosen
}

// healthCheck checks an individual backend's health via GET /health and updates state machine.
func (lb *LoadBalancer) healthCheck(b *Backend) {
	healthURL := fmt.Sprintf("%s://%s/health", b.URL.Scheme, b.URL.Host)
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		b.recordFailure()
		return
	}

	resp, err := lb.httpClient.Do(req)
	if err != nil {
		b.recordFailure()
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		b.recordSuccess()
	} else {
		b.recordFailure()
	}
}

// ── Metrics poller: /internal/health ─────────────────────────────────────────

// metricsLoop polls /internal/health on all backends every healthInterval
// and updates each backend's BackendMetrics and Score.
func (lb *LoadBalancer) metricsLoop() {
	ticker := time.NewTicker(lb.healthInterval)
	defer ticker.Stop()

	for range ticker.C {
		for _, b := range lb.backends {
			go lb.fetchMetrics(b)
		}
	}
}

type internalHealthResponse struct {
	Status         string  `json:"status"`
	CPU            float64 `json:"cpu"`
	Memory         float64 `json:"memory"`
	ActiveRequests float64 `json:"active_requests"`
	LatencyMs      float64 `json:"latency_ms"`
	Timestamp      int64   `json:"timestamp"`
}

func (lb *LoadBalancer) fetchMetrics(b *Backend) {
	metricsURL := fmt.Sprintf("%s://%s/internal/health", b.URL.Scheme, b.URL.Host)
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, metricsURL, nil)
	if err != nil {
		b.Metrics.mu.Lock()
		b.Metrics.Stale = true
		b.Metrics.mu.Unlock()
		return
	}

	resp, err := lb.httpClient.Do(req)
	if err != nil {
		b.Metrics.mu.Lock()
		b.Metrics.Stale = true
		b.Metrics.mu.Unlock()
		log.Printf("[METRICS] Backend %s unreachable for /internal/health (stale metrics)", b.URL.Host)
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return
	}

	var h internalHealthResponse
	if err := json.Unmarshal(body, &h); err != nil {
		return
	}

	b.Metrics.mu.Lock()
	b.Metrics.CPU = h.CPU
	b.Metrics.Memory = h.Memory
	// active_requests from backend is intentionally ignored here.
	// The LB uses b.InFlight.Load() instead (real-time, can't be stale).
	b.Metrics.LatencyEWMA = UpdateEWMA(h.LatencyMs, b.Metrics.LatencyEWMA, lb.cfg.EWMAAlpha)
	b.Metrics.LastSeen = time.Now()
	b.Metrics.Stale = false
	b.Metrics.mu.Unlock()
	// Score is computed live in getScore() via InFlight — no cached Score field needed.
}

// healthLoop runs periodic background health checks across all configured backends.
func (lb *LoadBalancer) healthLoop() {
	ticker := time.NewTicker(lb.healthInterval)
	defer ticker.Stop()

	for range ticker.C {
		var wg sync.WaitGroup
		for _, b := range lb.backends {
			wg.Add(1)
			go func(backend *Backend) {
				defer wg.Done()
				lb.healthCheck(backend)
			}(b)
		}
		wg.Wait()
	}
}

// recordLatency stores duration into metrics slice with mutex lock.
func (lb *LoadBalancer) recordLatency(d time.Duration) {
	lb.metrics.LatencyMu.Lock()
	lb.metrics.Latencies = append(lb.metrics.Latencies, d)
	lb.metrics.LatencyMu.Unlock()
}

// ServeHTTP handles incoming requests: routes to LB admin endpoints or proxies to backends.
func (lb *LoadBalancer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Management endpoints
	switch r.URL.Path {
	case "/lb/health":
		lb.handleLBHealth(w, r)
		return
	case "/lb/status":
		lb.handleLBStatus(w, r)
		return
	case "/lb/metrics":
		lb.handleLBMetrics(w, r)
		return
	case "/lb/reset":
		lb.handleLBReset(w, r)
		return
	}

	lb.metrics.Total.Add(1)
	start := time.Now()

	// Stamp X-Message-Id if not already set (ensures idempotent retries)
	if r.Header.Get("X-Message-Id") == "" {
		r.Header.Set("X-Message-Id", uuid.New().String())
	}

	backend := lb.nextBackend()
	if backend == nil {
		lb.metrics.Failed.Add(1)
		http.Error(w, `{"error": "no healthy backends available"}`, http.StatusServiceUnavailable)
		return
	}

	backend.InFlight.Add(1)
	defer backend.InFlight.Add(-1)

	recorder := &responseStatusRecorder{
		ResponseWriter: w,
		statusCode:     http.StatusOK,
	}

	backend.ReverseProxy.ServeHTTP(recorder, r)

	elapsed := time.Since(start)
	lb.recordLatency(elapsed)

	// Update backend EWMA latency from actual proxied round-trip
	b := backend
	b.Metrics.mu.Lock()
	b.Metrics.LatencyEWMA = UpdateEWMA(float64(elapsed.Milliseconds()), b.Metrics.LatencyEWMA, lb.cfg.EWMAAlpha)
	b.Metrics.mu.Unlock()
	// NOTE: No need to update Score here — getScore() computes live from InFlight.

	if recorder.statusCode >= 200 && recorder.statusCode < 400 {
		lb.metrics.Success.Add(1)
		backend.recordSuccess()
	} else if recorder.statusCode >= 500 {
		lb.metrics.Failed.Add(1)
		lb.metrics.BackendErrors.Add(1)
		backend.recordFailure()
		// Passive failure: try once more on a different backend
		lb.retryOnce(w, r, backend, start)
	} else {
		lb.metrics.Success.Add(1)
	}
}

// retryOnce attempts the request on a different healthy backend after a failure.
func (lb *LoadBalancer) retryOnce(w http.ResponseWriter, r *http.Request, failed *Backend, start time.Time) {
	for _, b := range lb.backends {
		if b == failed || !b.isRoutable() {
			continue
		}
		log.Printf("[RETRY] %s failed, retrying on %s", failed.URL.Host, b.URL.Host)
		b.InFlight.Add(1)
		defer b.InFlight.Add(-1)
		retryRecorder := &responseStatusRecorder{
			ResponseWriter: w,
			statusCode:     http.StatusOK,
		}
		b.ReverseProxy.ServeHTTP(retryRecorder, r)
		elapsed := time.Since(start)
		lb.recordLatency(elapsed)
		if retryRecorder.statusCode >= 200 && retryRecorder.statusCode < 400 {
			lb.metrics.Success.Add(1)
			lb.metrics.Failed.Add(^uint64(0)) // decrement — it was pre-incremented
		}
		return
	}
}

type responseStatusRecorder struct {
	http.ResponseWriter
	statusCode int
	wroteHead  bool
}

func (r *responseStatusRecorder) WriteHeader(code int) {
	if !r.wroteHead {
		r.statusCode = code
		r.wroteHead = true
		r.ResponseWriter.WriteHeader(code)
	}
}

func (r *responseStatusRecorder) Write(b []byte) (int, error) {
	if !r.wroteHead {
		r.WriteHeader(http.StatusOK)
	}
	return r.ResponseWriter.Write(b)
}

// Hijack supports WebSocket upgrading for full bi-directional communication
func (r *responseStatusRecorder) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if hijacker, ok := r.ResponseWriter.(http.Hijacker); ok {
		return hijacker.Hijack()
	}
	return nil, nil, fmt.Errorf("underlying ResponseWriter does not support Hijack")
}

func (lb *LoadBalancer) handleLBHealth(w http.ResponseWriter, r *http.Request) {
	healthyCount := 0
	for _, b := range lb.backends {
		if b.isRoutable() {
			healthyCount++
		}
	}

	status := "healthy"
	if healthyCount == 0 && len(lb.backends) > 0 {
		status = "all_backends_down"
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":           status,
		"total_backends":   len(lb.backends),
		"healthy_backends": healthyCount,
	})
}

func (lb *LoadBalancer) handleLBStatus(w http.ResponseWriter, r *http.Request) {
	type BackendStatus struct {
		URL      string      `json:"url"`
		State    string      `json:"state"`
		Score    float64     `json:"score"`
		InFlight int64       `json:"in_flight"`
		CPU      float64     `json:"cpu"`
		Memory   float64     `json:"memory"`
		Latency  float64     `json:"latency_ewma_ms"`
		Stale    bool        `json:"stale"`
	}

	var list []BackendStatus
	for _, b := range lb.backends {
		b.mu.Lock()
		state := b.State.String()
		b.mu.Unlock()
		b.Metrics.mu.Lock()
		cpu := b.Metrics.CPU
		mem := b.Metrics.Memory
		lat := b.Metrics.LatencyEWMA
		stale := b.Metrics.Stale
		b.Metrics.mu.Unlock()
		// InFlight is the LB-measured active request count (real-time, authoritative)
		inFlight := b.InFlight.Load()
		score := ComputeScore(b.Metrics, inFlight, lb.cfg)
		list = append(list, BackendStatus{
			URL:      b.URL.String(),
			State:    state,
			Score:    math.Round(score*1000) / 1000,
			InFlight: inFlight,
			CPU:      cpu,
			Memory:   mem,
			Latency:  lat,
			Stale:    stale,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"backends": list,
	})
}

func (lb *LoadBalancer) handleLBMetrics(w http.ResponseWriter, r *http.Request) {
	lb.metrics.LatencyMu.Lock()
	count := len(lb.metrics.Latencies)
	latenciesCopy := make([]time.Duration, count)
	copy(latenciesCopy, lb.metrics.Latencies)
	lb.metrics.LatencyMu.Unlock()

	var p50, p95, p99, avgMs float64
	if count > 0 {
		sort.Slice(latenciesCopy, func(i, j int) bool {
			return latenciesCopy[i] < latenciesCopy[j]
		})

		var totalDur time.Duration
		for _, d := range latenciesCopy {
			totalDur += d
		}
		avgMs = float64(totalDur.Milliseconds()) / float64(count)

		p50 = float64(latenciesCopy[int(float64(count)*0.50)].Microseconds()) / 1000.0
		p95 = float64(latenciesCopy[int(float64(count)*0.95)].Microseconds()) / 1000.0
		p99 = float64(latenciesCopy[int(float64(count)*0.99)].Microseconds()) / 1000.0
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"total":          lb.metrics.Total.Load(),
		"success":        lb.metrics.Success.Load(),
		"failed":         lb.metrics.Failed.Load(),
		"backend_errors": lb.metrics.BackendErrors.Load(),
		"switch_count":   lb.metrics.SwitchCount.Load(),
		"samples":        count,
		"avg_ms":         avgMs,
		"p50_ms":         p50,
		"p95_ms":         p95,
		"p99_ms":         p99,
	})
}

func (lb *LoadBalancer) handleLBReset(w http.ResponseWriter, r *http.Request) {
	lb.metrics.Total.Store(0)
	lb.metrics.Success.Store(0)
	lb.metrics.Failed.Store(0)
	lb.metrics.BackendErrors.Store(0)
	lb.metrics.SwitchCount.Store(0)
	lb.metrics.LatencyMu.Lock()
	lb.metrics.Latencies = nil
	lb.metrics.LatencyMu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"message": "metrics reset successfully",
	})
}

func main() {
	port            := flag.Int("port", 8080, "Port for Load Balancer to listen on (Sys1)")
	backendsArg     := flag.String("backends", "http://127.0.0.1:8081,http://127.0.0.1:8082,http://127.0.0.1:8083", "Comma-separated list of backend URLs")
	healthInterval  := flag.Duration("health-interval", 1*time.Second, "Interval between background health checks")
	backendTimeout  := flag.Duration("backend-timeout", 800*time.Millisecond, "Backend request timeout")
	overloadThresh  := flag.Float64("overload-threshold", defaultOverloadThreshold, "LoadScore above which a backend is considered overloaded")
	hysteresis      := flag.Float64("hysteresis", defaultHysteresis, "Min score gap required to switch backends (prevents flapping)")

	// Scoring weight flags (for experimental sweep — see experiments/weight_sweep.sh)
	wCPU   := flag.Float64("wcpu",       defaultWCpu, "Weight for CPU in scoring formula (0.0-1.0)")
	wLat   := flag.Float64("wlat",       defaultWLat, "Weight for Latency EWMA in scoring formula (0.0-1.0)")
	wMem   := flag.Float64("wmem",       defaultWMem, "Weight for Memory in scoring formula (0.0-1.0)")
	wAct   := flag.Float64("wact",       defaultWAct, "Weight for In-Flight requests in scoring formula (0.0-1.0)")
	ewmaA  := flag.Float64("ewma-alpha", defaultEWMAAlpha, "EWMA smoothing factor α (0.0=fully smooth, 1.0=no smoothing)")

	flag.Parse()

	cfg := DefaultScoringConfig()
	cfg.OverloadThreshold = *overloadThresh
	cfg.Hysteresis        = *hysteresis
	cfg.WCpu              = *wCPU
	cfg.WLat              = *wLat
	cfg.WMem              = *wMem
	cfg.WAct              = *wAct
	cfg.EWMAAlpha         = *ewmaA

	rawParts := strings.Split(*backendsArg, ",")
	var backendList []string
	for _, p := range rawParts {
		p = strings.TrimSpace(p)
		if p != "" {
			backendList = append(backendList, p)
		}
	}

	if len(backendList) == 0 {
		log.Fatal("No valid backend URLs provided via -backends flag")
	}

	lb := NewLoadBalancer(backendList, *healthInterval, *backendTimeout, cfg)

	// Start background health checking goroutine
	go lb.healthLoop()
	// Start background metrics polling goroutine (/internal/health)
	go lb.metricsLoop()

	addr := fmt.Sprintf("0.0.0.0:%d", *port)
	log.Printf("=============================================================")
	log.Printf(" 🚀 Load Balancer (Performance-Based) running on %s", addr)
	log.Printf(" 🎯 Backends (%d): %v", len(backendList), backendList)
	log.Printf(" ⏱️  Health Interval: %v | Timeout: %v", *healthInterval, *backendTimeout)
	log.Printf(" 📊 Scoring: overload=%.2f | hysteresis=%.2f | w_cpu=%.2f | w_lat=%.2f",
		cfg.OverloadThreshold, cfg.Hysteresis, cfg.WCpu, cfg.WLat)
	log.Printf(" 📊 Endpoints: /lb/health, /lb/status, /lb/metrics, /lb/reset")
	log.Printf("=============================================================")

	server := &http.Server{
		Addr:    addr,
		Handler: lb,
	}

	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Load Balancer HTTP server failed: %v", err)
	}
}
