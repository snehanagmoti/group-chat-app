package loadbalancer

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const affinityCookieName = "pixelchat_backend"

type Backend struct {
	ID       int
	URL      *url.URL
	Alive    atomic.Bool
	InFlight atomic.Int64
}

type latencyTracker struct {
	mu         sync.Mutex
	samples    []time.Duration
	next       int
	observed   uint64
	total      time.Duration
	maxSamples int
}

func newLatencyTracker(maxSamples int) *latencyTracker {
	return &latencyTracker{maxSamples: maxSamples}
}

func (tracker *latencyTracker) observe(duration time.Duration) {
	tracker.mu.Lock()
	defer tracker.mu.Unlock()

	tracker.observed++
	tracker.total += duration
	if tracker.maxSamples == 0 {
		return
	}
	if len(tracker.samples) < tracker.maxSamples {
		tracker.samples = append(tracker.samples, duration)
		return
	}
	tracker.samples[tracker.next] = duration
	tracker.next = (tracker.next + 1) % tracker.maxSamples
}

func (tracker *latencyTracker) snapshot() map[string]any {
	tracker.mu.Lock()
	samples := append([]time.Duration(nil), tracker.samples...)
	observed := tracker.observed
	total := tracker.total
	tracker.mu.Unlock()

	sort.Slice(samples, func(i, j int) bool { return samples[i] < samples[j] })
	averageMS := 0.0
	if observed > 0 {
		averageMS = durationMS(total / time.Duration(observed))
	}
	return map[string]any{
		"count":        observed,
		"sample_count": len(samples),
		"average_ms":   averageMS,
		"p50_ms":       percentileMS(samples, 50),
		"p95_ms":       percentileMS(samples, 95),
		"p99_ms":       percentileMS(samples, 99),
	}
}

type Metrics struct {
	Total         atomic.Uint64
	Success       atomic.Uint64
	Failed        atomic.Uint64
	BackendErrors atomic.Uint64
	latencies     *latencyTracker
}

func newMetrics(maxLatencySamples int) *Metrics {
	return &Metrics{latencies: newLatencyTracker(maxLatencySamples)}
}

type LoadBalancer struct {
	backends       []*Backend
	next           atomic.Uint64
	metrics        *Metrics
	transport      *http.Transport
	healthClient   *http.Client
	backendTimeout time.Duration
	healthPath     string
	affinity       bool
	secureCookie   bool
	logger         *log.Logger
	loadThreshold  int64
}

type config struct {
	backendsRaw               string
	port                      int
	healthInterval            time.Duration
	healthTimeout             time.Duration
	backendTimeout            time.Duration
	healthPath                string
	backendInsecureSkipVerify bool
	affinity                  bool
	tlsCert                   string
	tlsKey                    string
	maxLatencySamples         int
	loadThreshold             int
}

func parseBackends(raw string) ([]*Backend, error) {
	parts := strings.Split(raw, ",")
	backends := make([]*Backend, 0, len(parts))
	seen := make(map[string]struct{}, len(parts))

	for _, part := range parts {
		value := strings.TrimSpace(part)
		if value == "" {
			return nil, errors.New("backend URL list contains an empty entry")
		}
		parsed, err := url.Parse(value)
		if err != nil {
			return nil, fmt.Errorf("invalid backend URL %q: %w", value, err)
		}
		if (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
			return nil, fmt.Errorf("backend URL %q must include an http or https scheme and host", value)
		}
		if parsed.User != nil {
			return nil, fmt.Errorf("backend URL %q must not contain credentials", value)
		}
		parsed.Fragment = ""
		key := parsed.String()
		if _, duplicate := seen[key]; duplicate {
			return nil, fmt.Errorf("duplicate backend URL %q", value)
		}
		seen[key] = struct{}{}

		backend := &Backend{ID: len(backends), URL: parsed}
		backend.Alive.Store(true)
		backends = append(backends, backend)
	}
	if len(backends) == 0 {
		return nil, errors.New("at least one backend URL is required")
	}
	return backends, nil
}

func (lb *LoadBalancer) nextBackend(request *http.Request) *Backend {
	if len(lb.backends) == 0 {
		return nil
	}

	if lb.affinity && request != nil {
		if cookie, err := request.Cookie(affinityCookieName); err == nil {
			if index, err := strconv.Atoi(cookie.Value); err == nil &&
				index >= 0 && index < len(lb.backends) &&
				lb.backends[index].Alive.Load() &&
				(lb.loadThreshold <= 0 || lb.backends[index].InFlight.Load() < lb.loadThreshold) {
				return lb.backends[index]
			}
		}
	}

	for {
		start := lb.next.Load()
		currentIndex := start % uint64(len(lb.backends))
		currentBackend := lb.backends[currentIndex]
		
		if currentBackend.Alive.Load() && (lb.loadThreshold <= 0 || currentBackend.InFlight.Load() < lb.loadThreshold) {
			return currentBackend
		}

		found := false
		for offset := 1; offset <= len(lb.backends); offset++ {
			index := (start + uint64(offset)) % uint64(len(lb.backends))
			backend := lb.backends[index]
			if backend.Alive.Load() && (lb.loadThreshold <= 0 || backend.InFlight.Load() < lb.loadThreshold) {
				if lb.next.CompareAndSwap(start, start+uint64(offset)) {
					return backend
				}
				found = true
				break
			}
		}
		
		if !found {
			for offset := 1; offset <= len(lb.backends); offset++ {
				index := (start + uint64(offset)) % uint64(len(lb.backends))
				backend := lb.backends[index]
				if backend.Alive.Load() {
					if lb.next.CompareAndSwap(start, start+uint64(offset)) {
						return backend
					}
					found = true
					break
				}
			}
			if !found {
				return nil
			}
		}
	}
}

func (lb *LoadBalancer) backendHealthURL(backend *Backend) string {
	healthURL := *backend.URL
	healthURL.RawQuery = ""
	healthURL.Fragment = ""
	healthURL.Path = strings.TrimRight(healthURL.Path, "/") + "/" + strings.TrimLeft(lb.healthPath, "/")
	healthURL.RawPath = ""
	return healthURL.String()
}

func (lb *LoadBalancer) checkBackend(ctx context.Context, backend *Backend) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, lb.backendHealthURL(backend), nil)
	if err != nil {
		backend.Alive.Store(false)
		return
	}
	request.Header.Set("User-Agent", "PixelChat-Load-Balancer/1.0")

	response, err := lb.healthClient.Do(request)
	healthy := err == nil && response.StatusCode == http.StatusOK
	if response != nil {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		_ = response.Body.Close()
	}
	backend.Alive.Store(healthy)
}

func (lb *LoadBalancer) checkAllBackends(ctx context.Context) {
	var waitGroup sync.WaitGroup
	for _, backend := range lb.backends {
		waitGroup.Add(1)
		go func() {
			defer waitGroup.Done()
			lb.checkBackend(ctx, backend)
		}()
	}
	waitGroup.Wait()
}

func (lb *LoadBalancer) healthLoop(ctx context.Context, interval time.Duration) {
	lb.checkAllBackends(ctx)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			lb.checkAllBackends(ctx)
		case <-ctx.Done():
			return
		}
	}
}

func (lb *LoadBalancer) statusSnapshot() map[string]any {
	statuses := make([]map[string]any, 0, len(lb.backends))
	for _, backend := range lb.backends {
		statuses = append(statuses, map[string]any{
			"url":       backend.URL.String(),
			"alive":     backend.Alive.Load(),
			"in_flight": backend.InFlight.Load(),
		})
	}
	return map[string]any{"backends": statuses}
}

func (lb *LoadBalancer) metricsSnapshot() map[string]any {
	return map[string]any{
		"total":          lb.metrics.Total.Load(),
		"success":        lb.metrics.Success.Load(),
		"failed":         lb.metrics.Failed.Load(),
		"backend_errors": lb.metrics.BackendErrors.Load(),
		"latency":        lb.metrics.latencies.snapshot(),
	}
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	if err := json.NewEncoder(response).Encode(value); err != nil {
		log.Printf("encode JSON response: %v", err)
	}
}

func monitoringMethodAllowed(response http.ResponseWriter, request *http.Request) bool {
	if request.Method == http.MethodGet || request.Method == http.MethodHead {
		return true
	}
	response.Header().Set("Allow", "GET, HEAD")
	http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
	return false
}

func (lb *LoadBalancer) handler() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("/lb/health", func(response http.ResponseWriter, request *http.Request) {
		if !monitoringMethodAllowed(response, request) {
			return
		}
		writeJSON(response, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/lb/status", func(response http.ResponseWriter, request *http.Request) {
		if !monitoringMethodAllowed(response, request) {
			return
		}
		writeJSON(response, http.StatusOK, lb.statusSnapshot())
	})
	mux.HandleFunc("/lb/metrics", func(response http.ResponseWriter, request *http.Request) {
		if !monitoringMethodAllowed(response, request) {
			return
		}
		writeJSON(response, http.StatusOK, lb.metricsSnapshot())
	})
	mux.HandleFunc("/message", lb.fanOutMessage)
	mux.HandleFunc("/", lb.proxyRequest)
	return mux
}

// fanOutMessage replicates POST /message to ALL backends so every backend
// has the full message set and any /feed call returns complete data.
// Returns as soon as the FIRST backend succeeds (fire-and-forget the rest).
func (lb *LoadBalancer) fanOutMessage(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		lb.proxyRequest(response, request)
		return
	}

	start := time.Now()
	lb.metrics.Total.Add(1)

	// Read body once
	body, err := io.ReadAll(request.Body)
	request.Body.Close()
	if err != nil {
		lb.metrics.Failed.Add(1)
		http.Error(response, "failed to read request body", http.StatusBadRequest)
		return
	}

	type result struct {
		status int
		body   []byte
		header http.Header
		err    error
	}

	results := make(chan result, len(lb.backends))
	aliveCount := 0

	for _, backend := range lb.backends {
		if !backend.Alive.Load() {
			continue
		}
		aliveCount++
		go func(b *Backend) {
			b.InFlight.Add(1)
			defer b.InFlight.Add(-1)

			targetURL := *b.URL
			targetURL.Path = strings.TrimRight(targetURL.Path, "/") + "/message"

			ctx, cancel := context.WithTimeout(context.Background(), lb.backendTimeout)
			defer cancel()

			req, _ := http.NewRequestWithContext(ctx, http.MethodPost, targetURL.String(), bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")

			resp, err := lb.transport.RoundTrip(req)
			if err != nil {
				results <- result{err: err}
				return
			}
			defer resp.Body.Close()
			respBody, _ := io.ReadAll(resp.Body)
			results <- result{status: resp.StatusCode, body: respBody, header: resp.Header}
		}(backend)
	}

	if aliveCount == 0 {
		lb.metrics.Failed.Add(1)
		http.Error(response, "no healthy backends", http.StatusBadGateway)
		return
	}

	// Return on FIRST success — don't wait for all backends
	responded := false
	for i := 0; i < aliveCount; i++ {
		r := <-results
		if r.err != nil {
			continue
		}
		if !responded && r.status >= 200 && r.status < 400 {
			responded = true
			lb.metrics.latencies.observe(time.Since(start))
			lb.metrics.Success.Add(1)
			response.Header().Set("Content-Type", "application/json")
			response.WriteHeader(r.status)
			response.Write(r.body)
			// Don't return — drain the channel so goroutines don't leak
			go func() {
				for j := i + 1; j < aliveCount; j++ {
					<-results
				}
			}()
			return
		}
	}

	lb.metrics.latencies.observe(time.Since(start))
	lb.metrics.Failed.Add(1)
	http.Error(response, "all backends failed", http.StatusBadGateway)
}

func (lb *LoadBalancer) proxyRequest(response http.ResponseWriter, request *http.Request) {
	start := time.Now()
	lb.metrics.Total.Add(1)

	backend := lb.nextBackend(request)
	if backend == nil {
		lb.metrics.Failed.Add(1)
		lb.metrics.latencies.observe(time.Since(start))
		http.Error(response, "no healthy backends available", http.StatusServiceUnavailable)
		return
	}

	backend.InFlight.Add(1)
	defer backend.InFlight.Add(-1)

	recorder := &statusRecorder{ResponseWriter: response}
	proxyFailed := false
	backendFailed := false
	proxy := httputil.NewSingleHostReverseProxy(backend.URL)
	proxy.Transport = lb.transport
	proxy.ErrorLog = lb.logger
	proxy.ModifyResponse = func(backendResponse *http.Response) error {
		backendResponse.Header.Set("X-Load-Balancer-Backend", backend.URL.String())
		if lb.affinity {
			backendResponse.Header.Add("Set-Cookie", (&http.Cookie{
				Name:     affinityCookieName,
				Value:    strconv.Itoa(backend.ID),
				Path:     "/",
				MaxAge:   24 * 60 * 60,
				HttpOnly: true,
				Secure:   lb.secureCookie,
				SameSite: http.SameSiteLaxMode,
			}).String())
		}
		return nil
	}
	proxy.ErrorHandler = func(proxyResponse http.ResponseWriter, proxyRequest *http.Request, err error) {
		proxyFailed = true
		// A downstream client that times out or disconnects cancels the original
		// request context. That does not prove the selected backend is unhealthy,
		// so keep it in rotation. Proxy/backend timeouts occur while the original
		// client context is still live and continue to eject the backend.
		backendFailed = request.Context().Err() == nil
		if backendFailed {
			backend.Alive.Store(false)
		}
		if !recorder.wroteHeader {
			http.Error(proxyResponse, "backend unavailable", http.StatusBadGateway)
		}
	}

	proxyRequest := request
	cancel := func() {}
	if !isUpgradeRequest(request) {
		var contextWithTimeout context.Context
		contextWithTimeout, cancel = context.WithTimeout(request.Context(), lb.backendTimeout)
		proxyRequest = request.WithContext(contextWithTimeout)
	}
	defer cancel()

	proxy.ServeHTTP(recorder, proxyRequest)

	lb.metrics.latencies.observe(time.Since(start))
	status := recorder.status()
	if proxyFailed {
		lb.metrics.Failed.Add(1)
		if backendFailed {
			lb.metrics.BackendErrors.Add(1)
		}
		return
	}
	if status >= 100 && status < 400 {
		lb.metrics.Success.Add(1)
		return
	}
	lb.metrics.Failed.Add(1)
	if status >= 500 {
		lb.metrics.BackendErrors.Add(1)
	}
}

func isUpgradeRequest(request *http.Request) bool {
	if strings.TrimSpace(request.Header.Get("Upgrade")) == "" {
		return false
	}
	for _, token := range strings.Split(request.Header.Get("Connection"), ",") {
		if strings.EqualFold(strings.TrimSpace(token), "upgrade") {
			return true
		}
	}
	return false
}

type statusRecorder struct {
	http.ResponseWriter
	statusCode  int
	wroteHeader bool
}

func (recorder *statusRecorder) Unwrap() http.ResponseWriter {
	return recorder.ResponseWriter
}

func (recorder *statusRecorder) status() int {
	if recorder.statusCode == 0 {
		return http.StatusOK
	}
	return recorder.statusCode
}

func (recorder *statusRecorder) WriteHeader(statusCode int) {
	if recorder.wroteHeader {
		return
	}
	recorder.statusCode = statusCode
	recorder.wroteHeader = true
	recorder.ResponseWriter.WriteHeader(statusCode)
}

func (recorder *statusRecorder) Write(payload []byte) (int, error) {
	if !recorder.wroteHeader {
		recorder.WriteHeader(http.StatusOK)
	}
	return recorder.ResponseWriter.Write(payload)
}

func (recorder *statusRecorder) Flush() {
	if !recorder.wroteHeader {
		recorder.WriteHeader(http.StatusOK)
	}
	if flusher, ok := recorder.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (recorder *statusRecorder) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	hijacker, ok := recorder.ResponseWriter.(http.Hijacker)
	if !ok {
		return nil, nil, errors.New("underlying response writer does not support hijacking")
	}
	return hijacker.Hijack()
}

func (recorder *statusRecorder) Push(target string, options *http.PushOptions) error {
	if pusher, ok := recorder.ResponseWriter.(http.Pusher); ok {
		return pusher.Push(target, options)
	}
	return http.ErrNotSupported
}

func (recorder *statusRecorder) ReadFrom(source io.Reader) (int64, error) {
	if !recorder.wroteHeader {
		recorder.WriteHeader(http.StatusOK)
	}
	if readerFrom, ok := recorder.ResponseWriter.(io.ReaderFrom); ok {
		return readerFrom.ReadFrom(source)
	}
	return io.Copy(recorder.ResponseWriter, source)
}

func percentileMS(sortedDurations []time.Duration, percentile int) float64 {
	if len(sortedDurations) == 0 {
		return 0
	}
	index := (percentile*len(sortedDurations) + 99) / 100
	index = max(1, min(index, len(sortedDurations)))
	return durationMS(sortedDurations[index-1])
}

func durationMS(duration time.Duration) float64 {
	return float64(duration) / float64(time.Millisecond)
}

func validateConfig(configuration config) error {
	if strings.TrimSpace(configuration.backendsRaw) == "" {
		return errors.New("provide backend URLs using -backends")
	}
	if configuration.port < 1 || configuration.port > 65535 {
		return errors.New("-port must be between 1 and 65535")
	}
	if configuration.healthInterval <= 0 {
		return errors.New("-health-interval must be greater than zero")
	}
	if configuration.healthTimeout <= 0 {
		return errors.New("-health-timeout must be greater than zero")
	}
	if configuration.backendTimeout <= 0 {
		return errors.New("-backend-timeout must be greater than zero")
	}
	if strings.TrimSpace(configuration.healthPath) == "" {
		return errors.New("-health-path must not be empty")
	}
	if configuration.maxLatencySamples < 0 {
		return errors.New("-max-latency-samples must not be negative")
	}
	if (configuration.tlsCert == "") != (configuration.tlsKey == "") {
		return errors.New("-tls-cert and -tls-key must be provided together")
	}
	return nil
}

func newLoadBalancer(configuration config, backends []*Backend, logger *log.Logger) *LoadBalancer {
	tlsConfiguration := &tls.Config{
		MinVersion:         tls.VersionTLS12,
		InsecureSkipVerify: configuration.backendInsecureSkipVerify,
	}
	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          2000,
		MaxIdleConnsPerHost:   1000,
		MaxConnsPerHost:       0,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:  5 * time.Second,
		ResponseHeaderTimeout: configuration.backendTimeout,
		TLSClientConfig:       tlsConfiguration,
	}
	healthTransport := transport.Clone()
	healthTransport.MaxIdleConnsPerHost = 4

	return &LoadBalancer{
		backends:       backends,
		metrics:        newMetrics(configuration.maxLatencySamples),
		transport:      transport,
		healthClient:   &http.Client{Transport: healthTransport, Timeout: configuration.healthTimeout},
		backendTimeout: configuration.backendTimeout,
		healthPath:     configuration.healthPath,
		affinity:       configuration.affinity,
		secureCookie:   configuration.tlsCert != "",
		logger:         logger,
		loadThreshold:  int64(configuration.loadThreshold),
	}
}

func RunCLI(arguments []string, standardOutput io.Writer, standardError io.Writer) int {
	flags := flag.NewFlagSet("load-balancer", flag.ContinueOnError)
	flags.SetOutput(standardError)

	configuration := config{}
	flags.StringVar(&configuration.backendsRaw, "backends", "", "Comma-separated backend URLs")
	flags.IntVar(&configuration.port, "port", 8080, "Load balancer listen port")
	flags.DurationVar(&configuration.healthInterval, "health-interval", time.Second, "Health check interval")
	flags.DurationVar(&configuration.healthTimeout, "health-timeout", 2*time.Second, "Per-backend health check timeout")
	flags.DurationVar(&configuration.backendTimeout, "backend-timeout", 3*time.Second, "Total timeout for non-upgraded backend requests")
	flags.StringVar(&configuration.healthPath, "health-path", "/health", "Backend health check path")
	flags.BoolVar(&configuration.backendInsecureSkipVerify, "backend-insecure-skip-verify", false, "Accept self-signed backend TLS certificates (lab only)")
	flags.BoolVar(&configuration.affinity, "affinity", true, "Enable browser cookie affinity for stateful messaging sessions")
	flags.StringVar(&configuration.tlsCert, "tls-cert", "", "PEM certificate for HTTPS listener")
	flags.StringVar(&configuration.tlsKey, "tls-key", "", "PEM private key for HTTPS listener")
	flags.IntVar(&configuration.maxLatencySamples, "max-latency-samples", 100000, "Maximum recent latency samples kept for percentiles (0 disables samples)")
	flags.IntVar(&configuration.loadThreshold, "load-threshold", 100, "Maximum in-flight connections per backend before switching")

	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if err := validateConfig(configuration); err != nil {
		fmt.Fprintf(standardError, "load-balancer: %v\n", err)
		return 2
	}
	backends, err := parseBackends(configuration.backendsRaw)
	if err != nil {
		fmt.Fprintf(standardError, "load-balancer: %v\n", err)
		return 2
	}

	logger := log.New(standardError, "load-balancer: ", log.LstdFlags)
	balancer := newLoadBalancer(configuration, backends, logger)
	server := &http.Server{
		Addr:              fmt.Sprintf(":%d", configuration.port),
		Handler:           balancer.handler(),
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	balancer.checkAllBackends(ctx)
	go balancer.healthLoop(ctx, configuration.healthInterval)

	scheme := "http"
	if configuration.tlsCert != "" {
		scheme = "https"
	}
	fmt.Fprintf(standardOutput, "Load balancer listening on %s://localhost:%d with %d backend(s)\n", scheme, configuration.port, len(backends))

	errorChannel := make(chan error, 1)
	go func() {
		if configuration.tlsCert != "" {
			errorChannel <- server.ListenAndServeTLS(configuration.tlsCert, configuration.tlsKey)
			return
		}
		errorChannel <- server.ListenAndServe()
	}()

	select {
	case serveError := <-errorChannel:
		if serveError != nil && !errors.Is(serveError, http.ErrServerClosed) {
			logger.Printf("server failed: %v", serveError)
			return 1
		}
	case <-ctx.Done():
		shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownContext); err != nil {
			logger.Printf("graceful shutdown failed: %v", err)
			return 1
		}
	}
	return 0
}
