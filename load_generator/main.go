package main

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// ── Result types ───────────────────────────────────────────────────────────────

type RequestResult struct {
	Success    bool
	Latency    time.Duration
	StatusCode int
	Backend    string
	Err        error
	Op         string // "message" or "feed"
}

type ExperimentResult struct {
	Experiment     string         `json:"experiment"`
	TargetURL      string         `json:"target_url"`
	Requests       int            `json:"requests"`
	Concurrency    int            `json:"concurrency"`
	Successful     int            `json:"successful"`
	Failed         int            `json:"failed"`
	ElapsedTimeSec float64        `json:"elapsed_time_sec"`
	ThroughputRPS  float64        `json:"throughput_rps"`
	DropoutPercent float64        `json:"dropout_percent"`
	P50Ms          float64        `json:"p50_ms"`
	P95Ms          float64        `json:"p95_ms"`
	P99Ms          float64        `json:"p99_ms"`
	MinMs          float64        `json:"min_ms"`
	MaxMs          float64        `json:"max_ms"`
	AvgMs          float64        `json:"avg_ms"`
	BackendCounts  map[string]int `json:"backend_distribution"`
	StatusCodeMap  map[int]int    `json:"status_codes"`
	Timestamp      string         `json:"timestamp"`
}

// ── Random message generator ────────────────────────────────────────────────

const letterBytes = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,!?-"

// randomMessage generates a random message string between minLen and maxLen chars.
func randomMessage(rng *rand.Rand, minLen, maxLen int) string {
	if minLen <= 0 {
		minLen = 10
	}
	if maxLen < minLen {
		maxLen = minLen
	}
	n := minLen + rng.Intn(maxLen-minLen+1)
	b := make([]byte, n)
	for i := range b {
		b[i] = letterBytes[rng.Intn(len(letterBytes))]
	}
	return string(b)
}

// randomUser picks a username like "User42" from a pool of numUsers.
func randomUser(rng *rand.Rand, numUsers int) string {
	return fmt.Sprintf("User%d", 1+rng.Intn(numUsers))
}

// ── HTTP helpers ───────────────────────────────────────────────────────────────

type BackendJSONResponse struct {
	Backend   string `json:"backend"`
	RequestID uint64 `json:"request_id"`
	Message   string `json:"message"`
}

func extractBackend(body []byte) string {
	var bResp BackendJSONResponse
	if err := json.Unmarshal(body, &bResp); err == nil && bResp.Backend != "" {
		return bResp.Backend
	}
	return "unknown"
}

func buildClient(timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			DialContext: (&net.Dialer{
				Timeout:   3 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			ForceAttemptHTTP2:   false,
			MaxIdleConns:        2000,
			MaxIdleConnsPerHost: 500,
			IdleConnTimeout:     90 * time.Second,
			DisableCompression:  true,
		},
	}
}

// doPostMessage sends POST /message and returns a result.
func doPostMessage(client *http.Client, baseURL, clientName, msg string, workerID int) RequestResult {
	start := time.Now()
	payload := map[string]string{
		"client-name": clientName,
		"msg":         msg,
	}
	data, _ := json.Marshal(payload)

	req, err := http.NewRequest(http.MethodPost, baseURL+"/message", bytes.NewReader(data))
	if err != nil {
		return RequestResult{Success: false, Latency: time.Since(start), Err: err, Op: "message"}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", fmt.Sprintf("LoadGen-Worker-%d", workerID))

	resp, err := client.Do(req)
	lat := time.Since(start)
	if err != nil {
		return RequestResult{Success: false, Latency: lat, Err: err, Op: "message"}
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()

	return RequestResult{
		Success:    resp.StatusCode >= 200 && resp.StatusCode < 400,
		Latency:    lat,
		StatusCode: resp.StatusCode,
		Backend:    extractBackend(body),
		Op:         "message",
	}
}

// doGetFeed sends GET /feed and returns a result.
func doGetFeed(client *http.Client, baseURL string, workerID int) RequestResult {
	start := time.Now()
	req, err := http.NewRequest(http.MethodGet, baseURL+"/feed", nil)
	if err != nil {
		return RequestResult{Success: false, Latency: time.Since(start), Err: err, Op: "feed"}
	}
	req.Header.Set("User-Agent", fmt.Sprintf("LoadGen-Worker-%d", workerID))

	resp, err := client.Do(req)
	lat := time.Since(start)
	if err != nil {
		return RequestResult{Success: false, Latency: lat, Err: err, Op: "feed"}
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()

	return RequestResult{
		Success:    resp.StatusCode >= 200 && resp.StatusCode < 400,
		Latency:    lat,
		StatusCode: resp.StatusCode,
		Backend:    extractBackend(body),
		Op:         "feed",
	}
}

// ── Main ────────────────────────────────────────────────────────────────────

func main() {
	// ── CLI flags ──────────────────────────────────────────────────────────
	targetURL   := flag.String("url", "http://127.0.0.1:8080", "Load Balancer base URL")
	numRequests := flag.Int("requests", 5000, "Total number of requests")
	concurrency := flag.Int("concurrency", 40, "Number of concurrent workers")
	timeout     := flag.Duration("timeout", 5*time.Second, "Request timeout")
	experiment  := flag.String("experiment", "baseline", "Experiment name")
	outFile     := flag.String("out", "", "Output JSON file path")
	csvFile     := flag.String("csv", "", "Output CSV file path")

	// Variable load parameters (assignment requirements)
	numUsers   := flag.Int("users", 50, "Number of distinct virtual users (client-names)")
	minMsgLen  := flag.Int("min-msg-len", 10, "Minimum message length in characters")
	maxMsgLen  := flag.Int("max-msg-len", 200, "Maximum message length in characters")
	minJitterMs := flag.Int("min-jitter-ms", 0, "Minimum sleep between requests per worker (ms)")
	maxJitterMs := flag.Int("max-jitter-ms", 0, "Maximum sleep between requests per worker (ms)")
	// feedRatio: fraction of requests that are GET /feed (rest are POST /message)
	feedRatio  := flag.Float64("feed-ratio", 0.2, "Fraction of requests that are GET /feed (0.0–1.0)")

	flag.Parse()

	baseURL := strings.TrimRight(*targetURL, "/")

	log.Printf("=============================================================")
	log.Printf(" ⚡ LOAD GENERATOR — Chat App")
	log.Printf(" 🎯 Target : %s", baseURL)
	log.Printf(" 🔢 Requests: %d | 🧵 Workers: %d", *numRequests, *concurrency)
	log.Printf(" 👥 Users: %d | Msg length: %d–%d chars", *numUsers, *minMsgLen, *maxMsgLen)
	log.Printf(" 📊 Feed ratio: %.0f%% GET /feed, %.0f%% POST /message",
		*feedRatio*100, (1-*feedRatio)*100)
	if *maxJitterMs > 0 {
		log.Printf(" 💤 Jitter: %d–%dms per request", *minJitterMs, *maxJitterMs)
	}
	log.Printf(" 🧪 Experiment: %s", *experiment)
	log.Printf("=============================================================")

	client := buildClient(*timeout)

	jobs    := make(chan int, *numRequests)
	results := make(chan RequestResult, *numRequests)

	var wg sync.WaitGroup
	startTime := time.Now()

	// Launch worker pool
	for w := 1; w <= *concurrency; w++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			rng := rand.New(rand.NewSource(time.Now().UnixNano() + int64(workerID)))
			for range jobs {
				// Optional jitter: random sleep to simulate realistic user behaviour
				if *maxJitterMs > 0 {
					jitter := *minJitterMs
					if *maxJitterMs > *minJitterMs {
						jitter += rng.Intn(*maxJitterMs - *minJitterMs + 1)
					}
					if jitter > 0 {
						time.Sleep(time.Duration(jitter) * time.Millisecond)
					}
				}

				// Decide: GET /feed or POST /message?
				var res RequestResult
				if rng.Float64() < *feedRatio {
					res = doGetFeed(client, baseURL, workerID)
				} else {
					user := randomUser(rng, *numUsers)
					msg  := randomMessage(rng, *minMsgLen, *maxMsgLen)
					res  = doPostMessage(client, baseURL, user, msg, workerID)
				}
				results <- res
			}
		}(w)
	}

	// Enqueue all jobs
	for i := 1; i <= *numRequests; i++ {
		jobs <- i
	}
	close(jobs)

	wg.Wait()
	close(results)

	totalElapsed := time.Since(startTime)

	// ── Aggregate metrics ──────────────────────────────────────────────────
	var successCount, failCount int
	var latencies []time.Duration
	var totalLatency time.Duration
	backendCounts := make(map[string]int)
	statusCodes   := make(map[int]int)
	opCounts      := map[string]int{"message": 0, "feed": 0}

	for res := range results {
		latencies = append(latencies, res.Latency)
		totalLatency += res.Latency
		opCounts[res.Op]++
		if res.StatusCode > 0 {
			statusCodes[res.StatusCode]++
		}
		if res.Success {
			successCount++
			if res.Backend != "" && res.Backend != "unknown" {
				backendCounts[res.Backend]++
			}
		} else {
			failCount++
		}
	}

	sort.Slice(latencies, func(i, j int) bool { return latencies[i] < latencies[j] })

	totalReq := len(latencies)
	var p50, p95, p99, minMs, maxMs, avgMs float64
	if totalReq > 0 {
		minMs = float64(latencies[0].Microseconds()) / 1000.0
		maxMs = float64(latencies[totalReq-1].Microseconds()) / 1000.0
		avgMs = float64(totalLatency.Milliseconds()) / float64(totalReq)
		p50   = float64(latencies[int(float64(totalReq)*0.50)].Microseconds()) / 1000.0
		p95   = float64(latencies[int(float64(totalReq)*0.95)].Microseconds()) / 1000.0
		p99   = float64(latencies[int(float64(totalReq)*0.99)].Microseconds()) / 1000.0
	}

	throughput  := float64(successCount) / totalElapsed.Seconds()
	dropoutPct  := (float64(failCount) / float64(totalReq)) * 100.0

	// ── Print summary ──────────────────────────────────────────────────────
	fmt.Println("\n" + strings.Repeat("=", 70))
	fmt.Printf("              EXPERIMENT SUMMARY: %s\n", *experiment)
	fmt.Println(strings.Repeat("=", 70))
	fmt.Printf(" Target              : %s\n", baseURL)
	fmt.Printf(" Total Requests      : %d\n", *numRequests)
	fmt.Printf(" Concurrency         : %d workers\n", *concurrency)
	fmt.Printf(" Virtual Users       : %d | Msg: %d–%d chars\n", *numUsers, *minMsgLen, *maxMsgLen)
	fmt.Printf(" POST /message       : %d requests\n", opCounts["message"])
	fmt.Printf(" GET  /feed          : %d requests\n", opCounts["feed"])
	fmt.Printf(" Elapsed Time        : %.3f s\n", totalElapsed.Seconds())
	fmt.Printf(" Successful          : %d (%.2f%%)\n", successCount, float64(successCount)/float64(*numRequests)*100.0)
	fmt.Printf(" Failed (Dropout)    : %d (%.2f%%)\n", failCount, dropoutPct)
	fmt.Printf(" Throughput (RPS)    : \033[1;32m%.2f req/sec\033[0m\n", throughput)
	fmt.Printf(" Latency p50 (Med)   : %.2f ms\n", p50)
	fmt.Printf(" Latency p95         : %.2f ms\n", p95)
	fmt.Printf(" Latency p99 (Tail)  : %.2f ms\n", p99)
	fmt.Printf(" Latency Avg         : %.2f ms (Min: %.2f ms, Max: %.2f ms)\n", avgMs, minMs, maxMs)
	if len(backendCounts) > 0 {
		fmt.Printf(" Backend Distribution:\n")
		for b, count := range backendCounts {
			fmt.Printf("   • %-20s : %d (%.1f%%)\n", b, count, float64(count)/float64(successCount)*100.0)
		}
	}
	fmt.Println(strings.Repeat("=", 70) + "\n")

	expResult := ExperimentResult{
		Experiment:     *experiment,
		TargetURL:      baseURL,
		Requests:       *numRequests,
		Concurrency:    *concurrency,
		Successful:     successCount,
		Failed:         failCount,
		ElapsedTimeSec: totalElapsed.Seconds(),
		ThroughputRPS:  throughput,
		DropoutPercent: dropoutPct,
		P50Ms:          p50,
		P95Ms:          p95,
		P99Ms:          p99,
		MinMs:          minMs,
		MaxMs:          maxMs,
		AvgMs:          avgMs,
		BackendCounts:  backendCounts,
		StatusCodeMap:  statusCodes,
		Timestamp:      time.Now().Format(time.RFC3339),
	}

	if *outFile != "" {
		jsonData, _ := json.MarshalIndent(expResult, "", "  ")
		if err := os.WriteFile(*outFile, jsonData, 0644); err != nil {
			log.Printf("Failed to write JSON: %v", err)
		} else {
			log.Printf("📁 Saved JSON report: %s", *outFile)
		}
	}

	if *csvFile != "" {
		writeCSV(*csvFile, expResult)
	}
}

func writeCSV(filePath string, res ExperimentResult) {
	fileExists := false
	if _, err := os.Stat(filePath); err == nil {
		fileExists = true
	}
	f, err := os.OpenFile(filePath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		log.Printf("Failed to open CSV: %v", err)
		return
	}
	defer f.Close()

	w := csv.NewWriter(f)
	defer w.Flush()

	if !fileExists {
		w.Write([]string{
			"Experiment", "Requests", "Concurrency", "Success", "Failed",
			"RPS", "DropoutPercent", "p50_ms", "p95_ms", "p99_ms", "avg_ms", "ElapsedSec",
		})
	}
	w.Write([]string{
		res.Experiment,
		fmt.Sprintf("%d", res.Requests),
		fmt.Sprintf("%d", res.Concurrency),
		fmt.Sprintf("%d", res.Successful),
		fmt.Sprintf("%d", res.Failed),
		fmt.Sprintf("%.2f", res.ThroughputRPS),
		fmt.Sprintf("%.2f%%", res.DropoutPercent),
		fmt.Sprintf("%.2fms", res.P50Ms),
		fmt.Sprintf("%.2fms", res.P95Ms),
		fmt.Sprintf("%.2fms", res.P99Ms),
		fmt.Sprintf("%.2fms", res.AvgMs),
		fmt.Sprintf("%.3f", res.ElapsedTimeSec),
	})
	log.Printf("📊 Appended row to CSV: %s", filePath)
}
