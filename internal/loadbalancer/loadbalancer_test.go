package loadbalancer

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestLab6FeedKeepsEveryRepeatedSubmission(t *testing.T) {
	balancer := testBalancer(t, "http://one.test", nil)
	server := httptest.NewServer(balancer.handler())
	defer server.Close()

	const requestCount = 200
	body := []byte(`{"client-name":"load-user","msg":"same-message"}`)
	errors := make(chan error, requestCount)
	var waitGroup sync.WaitGroup
	for range requestCount {
		waitGroup.Add(1)
		go func() {
			defer waitGroup.Done()
			response, err := http.Post(server.URL+"/message", "application/json", bytes.NewReader(body))
			if err != nil {
				errors <- err
				return
			}
			defer response.Body.Close()
			if response.StatusCode != http.StatusOK {
				errors <- fmt.Errorf("POST status = %d", response.StatusCode)
			}
		}()
	}
	waitGroup.Wait()
	close(errors)
	for err := range errors {
		t.Error(err)
	}
	if t.Failed() {
		return
	}

	response, err := http.Get(server.URL + "/feed")
	if err != nil {
		t.Fatalf("GET /feed: %v", err)
	}
	defer response.Body.Close()
	var feed struct {
		Messages []map[string]string `json:"messages"`
	}
	if err := json.NewDecoder(response.Body).Decode(&feed); err != nil {
		t.Fatalf("decode feed: %v", err)
	}
	if len(feed.Messages) != requestCount {
		t.Fatalf("feed length = %d, want %d", len(feed.Messages), requestCount)
	}
	for index, message := range feed.Messages {
		if message["client-name"] != "load-user" || message["msg"] != "same-message" {
			t.Fatalf("message %d changed: %#v", index, message)
		}
	}
}

func TestLab6ClearAndValidation(t *testing.T) {
	balancer := testBalancer(t, "http://one.test", nil)
	server := httptest.NewServer(balancer.handler())
	defer server.Close()

	response, err := http.Post(server.URL+"/message", "application/json", strings.NewReader(`[]`))
	if err != nil {
		t.Fatalf("invalid POST: %v", err)
	}
	_ = response.Body.Close()
	if response.StatusCode != http.StatusBadRequest {
		t.Fatalf("invalid POST status = %d, want 400", response.StatusCode)
	}

	response, err = http.Post(server.URL+"/message", "application/json", strings.NewReader(`{"msg":"kept"}`))
	if err != nil {
		t.Fatalf("valid POST: %v", err)
	}
	_ = response.Body.Close()

	response, err = http.Post(server.URL+"/clear", "application/json", nil)
	if err != nil {
		t.Fatalf("POST /clear: %v", err)
	}
	_ = response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("clear status = %d, want 200", response.StatusCode)
	}

	response, err = http.Get(server.URL + "/feed")
	if err != nil {
		t.Fatalf("GET /feed: %v", err)
	}
	defer response.Body.Close()
	feedBody, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatalf("read feed: %v", err)
	}
	if string(feedBody) != `{"messages":[]}` {
		t.Fatalf("feed after clear = %q", feedBody)
	}
}

func TestLab6FeedAlwaysReturnsPlainJSON(t *testing.T) {
	balancer := testBalancer(t, "http://one.test", nil)
	server := httptest.NewServer(balancer.handler())
	defer server.Close()

	request, err := http.NewRequest(http.MethodGet, server.URL+"/feed", nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Accept-Encoding", "gzip")
	client := &http.Client{Transport: &http.Transport{DisableCompression: true}}
	response, err := client.Do(request)
	if err != nil {
		t.Fatalf("GET /feed: %v", err)
	}
	defer response.Body.Close()
	if encoding := response.Header.Get("Content-Encoding"); encoding != "" {
		t.Fatalf("Content-Encoding = %q, want plain JSON", encoding)
	}
	feedBody, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatalf("read feed: %v", err)
	}
	if string(feedBody) != `{"messages":[]}` {
		t.Fatalf("feed = %q", feedBody)
	}
}

func testConfig() config {
	return config{
		port:              8080,
		healthInterval:    time.Second,
		healthTimeout:     100 * time.Millisecond,
		backendTimeout:    250 * time.Millisecond,
		healthPath:        "/health",
		affinity:          false,
		maxLatencySamples: 100,
	}
}

func testBalancer(t *testing.T, rawURLs string, mutate func(*config)) *LoadBalancer {
	t.Helper()
	configuration := testConfig()
	configuration.backendsRaw = rawURLs
	if mutate != nil {
		mutate(&configuration)
	}
	backends, err := parseBackends(rawURLs)
	if err != nil {
		t.Fatalf("parse backends: %v", err)
	}
	return newLoadBalancer(configuration, backends, log.New(io.Discard, "", 0))
}

func namedBackend(name string) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/health" {
			response.WriteHeader(http.StatusOK)
			return
		}
		response.Header().Set("X-Backend-Name", name)
		_, _ = io.WriteString(response, name)
	}))
}

func TestParseBackendsValidation(t *testing.T) {
	for _, raw := range []string{
		"",
		"localhost:8080",
		"ftp://localhost:21",
		"http://user:password@localhost:8080",
		"http://localhost:8080,",
		"http://localhost:8080,http://localhost:8080",
	} {
		t.Run(raw, func(t *testing.T) {
			if _, err := parseBackends(raw); err == nil {
				t.Fatalf("parseBackends(%q) unexpectedly succeeded", raw)
			}
		})
	}
}

func TestRoundRobinStartsAtFirstBackendAndSkipsUnhealthy(t *testing.T) {
	balancer := testBalancer(t, "http://one.test,http://two.test,http://three.test", nil)
	if got := balancer.nextBackend(nil).ID; got != 0 {
		t.Fatalf("first backend ID = %d, want 0", got)
	}
	balancer.backends[1].Alive.Store(false)
	got := []int{balancer.nextBackend(nil).ID, balancer.nextBackend(nil).ID, balancer.nextBackend(nil).ID}
	want := []int{2, 0, 2}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("selection %v, want %v", got, want)
		}
	}
}

func TestProxyDistributesAndPublishesMetrics(t *testing.T) {
	first := namedBackend("one")
	defer first.Close()
	second := namedBackend("two")
	defer second.Close()

	balancer := testBalancer(t, first.URL+","+second.URL, nil)
	balancer.checkAllBackends(context.Background())
	proxy := httptest.NewServer(balancer.handler())
	defer proxy.Close()

	counts := map[string]int{}
	for request := 0; request < 6; request++ {
		response, err := http.Get(proxy.URL + "/work?value=1")
		if err != nil {
			t.Fatalf("proxy request: %v", err)
		}
		body, err := io.ReadAll(response.Body)
		_ = response.Body.Close()
		if err != nil {
			t.Fatalf("read response: %v", err)
		}
		counts[string(body)]++
	}
	if counts["one"] != 3 || counts["two"] != 3 {
		t.Fatalf("round-robin counts = %#v, want 3 each", counts)
	}

	metrics := balancer.metricsSnapshot()
	if metrics["total"] != uint64(6) || metrics["success"] != uint64(6) || metrics["failed"] != uint64(0) {
		t.Fatalf("unexpected metrics: %#v", metrics)
	}
	latency := metrics["latency"].(map[string]any)
	if latency["count"] != uint64(6) {
		t.Fatalf("latency count = %#v, want 6", latency["count"])
	}
}

func TestClientCancellationDoesNotMarkBackendUnhealthy(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		select {
		case <-time.After(500 * time.Millisecond):
			response.WriteHeader(http.StatusOK)
		case <-request.Context().Done():
			return
		}
	}))
	defer backend.Close()

	balancer := testBalancer(t, backend.URL, func(configuration *config) {
		configuration.backendTimeout = 2 * time.Second
	})
	proxy := httptest.NewServer(balancer.handler())
	defer proxy.Close()

	client := &http.Client{Timeout: 40 * time.Millisecond}
	if _, err := client.Get(proxy.URL + "/slow"); err == nil {
		t.Fatal("request unexpectedly completed before client timeout")
	}

	deadline := time.Now().Add(time.Second)
	for balancer.backends[0].InFlight.Load() != 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !balancer.backends[0].Alive.Load() {
		t.Fatal("client cancellation marked a healthy backend down")
	}
	if got := balancer.metrics.BackendErrors.Load(); got != 0 {
		t.Fatalf("backend error count = %d, want 0 for client cancellation", got)
	}
}

func TestCookieAffinityKeepsStatefulClientOnOneBackend(t *testing.T) {
	first := namedBackend("one")
	defer first.Close()
	second := namedBackend("two")
	defer second.Close()

	balancer := testBalancer(t, first.URL+","+second.URL, func(configuration *config) {
		configuration.affinity = true
	})
	proxy := httptest.NewServer(balancer.handler())
	defer proxy.Close()

	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatalf("cookie jar: %v", err)
	}
	client := &http.Client{Jar: jar}
	var names []string
	for request := 0; request < 3; request++ {
		response, err := client.Get(proxy.URL + "/rooms")
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		body, err := io.ReadAll(response.Body)
		_ = response.Body.Close()
		if err != nil {
			t.Fatalf("read response: %v", err)
		}
		names = append(names, string(body))
	}
	if names[0] != names[1] || names[1] != names[2] {
		t.Fatalf("affinity responses = %v, want one backend", names)
	}
}

func TestBackendTimeoutReturnsBadGatewayAndMarksBackendDown(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		time.Sleep(100 * time.Millisecond)
		_, _ = io.WriteString(response, "late")
	}))
	defer backend.Close()

	balancer := testBalancer(t, backend.URL, func(configuration *config) {
		configuration.backendTimeout = 20 * time.Millisecond
	})
	proxy := httptest.NewServer(balancer.handler())
	defer proxy.Close()

	response, err := http.Get(proxy.URL + "/slow")
	if err != nil {
		t.Fatalf("proxy request: %v", err)
	}
	_ = response.Body.Close()
	if response.StatusCode != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", response.StatusCode)
	}
	if balancer.backends[0].Alive.Load() {
		t.Fatal("timed-out backend remained healthy")
	}
	if balancer.metrics.Failed.Load() != 1 || balancer.metrics.BackendErrors.Load() != 1 {
		t.Fatalf("failed=%d backend_errors=%d, want 1/1", balancer.metrics.Failed.Load(), balancer.metrics.BackendErrors.Load())
	}
}

func TestUpgradeConnectionIsProxied(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if !isUpgradeRequest(request) {
			http.Error(response, "upgrade required", http.StatusBadRequest)
			return
		}
		hijacker, ok := response.(http.Hijacker)
		if !ok {
			t.Error("backend response writer cannot hijack")
			return
		}
		connection, buffered, err := hijacker.Hijack()
		if err != nil {
			t.Errorf("backend hijack: %v", err)
			return
		}
		defer connection.Close()
		_, _ = buffered.WriteString("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: echo\r\n\r\n")
		_ = buffered.Flush()
		payload := make([]byte, 4)
		if _, err := io.ReadFull(buffered, payload); err == nil {
			_, _ = connection.Write(payload)
		}
	}))
	defer backend.Close()

	balancer := testBalancer(t, backend.URL, nil)
	proxy := httptest.NewServer(balancer.handler())
	defer proxy.Close()

	address := strings.TrimPrefix(proxy.URL, "http://")
	connection, err := net.DialTimeout("tcp", address, time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(2 * time.Second))
	_, _ = fmt.Fprintf(connection, "GET /ws HTTP/1.1\r\nHost: %s\r\nConnection: Upgrade\r\nUpgrade: echo\r\n\r\n", address)

	reader := bufio.NewReader(connection)
	request, _ := http.NewRequest(http.MethodGet, proxy.URL+"/ws", nil)
	response, err := http.ReadResponse(reader, request)
	if err != nil {
		t.Fatalf("read upgrade response: %v", err)
	}
	if response.StatusCode != http.StatusSwitchingProtocols {
		t.Fatalf("status = %d, want 101", response.StatusCode)
	}
	if _, err := connection.Write([]byte("ping")); err != nil {
		t.Fatalf("write upgraded payload: %v", err)
	}
	echo := make([]byte, 4)
	if _, err := io.ReadFull(reader, echo); err != nil {
		t.Fatalf("read upgraded payload: %v", err)
	}
	if string(echo) != "ping" {
		t.Fatalf("echo = %q, want ping", echo)
	}
}

func TestHealthCheckStateTransitions(t *testing.T) {
	var healthy atomic.Bool
	healthy.Store(false)
	backend := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if healthy.Load() {
			response.WriteHeader(http.StatusOK)
			return
		}
		http.Error(response, "down", http.StatusServiceUnavailable)
	}))
	defer backend.Close()

	balancer := testBalancer(t, backend.URL, nil)
	balancer.checkBackend(context.Background(), balancer.backends[0])
	if balancer.backends[0].Alive.Load() {
		t.Fatal("unhealthy backend marked alive")
	}
	healthy.Store(true)
	balancer.checkBackend(context.Background(), balancer.backends[0])
	if !balancer.backends[0].Alive.Load() {
		t.Fatal("recovered backend remained down")
	}
}

func TestLatencyTrackerIsBounded(t *testing.T) {
	tracker := newLatencyTracker(3)
	for value := 1; value <= 5; value++ {
		tracker.observe(time.Duration(value) * time.Millisecond)
	}
	snapshot := tracker.snapshot()
	if snapshot["count"] != uint64(5) || snapshot["sample_count"] != 3 {
		t.Fatalf("unexpected snapshot: %#v", snapshot)
	}
	if got := snapshot["p50_ms"].(float64); got != 4 {
		t.Fatalf("p50 = %v, want 4", got)
	}
}
