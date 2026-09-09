package main

import (
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
)

// ─── Configuration ────────────────────────────────────────────────────────────

type Config struct {
	URL           string
	Users         int
	Duration      time.Duration
	MinIntervalMs int
	MaxIntervalMs int
	MinMsgLen     int
	MaxMsgLen     int
	ReadRatio     float64
	Experiment    string
	OutDir        string
	RampSeconds   int
}

// ─── Result Types ─────────────────────────────────────────────────────────────

type PerSecondSample struct {
	Second  int     `json:"second"`
	RPS     float64 `json:"rps"`
	ErrRate float64 `json:"err_rate"`
}

type Result struct {
	Experiment     string  `json:"experiment"`
	Users          int     `json:"users"`
	DurationSec    float64 `json:"duration_sec"`
	Successful     int64   `json:"successful"`
	Failed         int64   `json:"failed"`
	Total          int64   `json:"total"`
	ThroughputRPS  float64 `json:"throughput_rps"`
	DropoutPercent float64 `json:"dropout_percent"`
	P50Ms          float64 `json:"p50_ms"`
	P95Ms          float64 `json:"p95_ms"`
	P99Ms          float64 `json:"p99_ms"`
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "

func randomString(rng *rand.Rand, n int) string {
	b := make([]byte, n)
	for i := range b {
		b[i] = letters[rng.Intn(len(letters))]
	}
	return string(b)
}

func doGet(client *http.Client, targetURL string) (time.Duration, error) {
	start := time.Now()
	resp, err := client.Get(targetURL)
	lat := time.Since(start)
	if err != nil {
		return lat, err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 400 {
		return lat, fmt.Errorf("HTTP status %d", resp.StatusCode)
	}
	return lat, nil
}

func doPost(client *http.Client, targetURL, clientName, msg, msgID string) (time.Duration, error) {
	payload := map[string]string{
		"client-name": clientName,
		"msg":         msg,
		"msg_id":      msgID,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return 0, err
	}

	start := time.Now()
	resp, err := client.Post(targetURL, "application/json", bytes.NewReader(body))
	lat := time.Since(start)
	if err != nil {
		return lat, err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 400 {
		return lat, fmt.Errorf("HTTP status %d", resp.StatusCode)
	}
	return lat, nil
}

func percentileMs(sorted []time.Duration, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := int(float64(len(sorted)-1) * p / 100.0)
	return float64(sorted[idx].Microseconds()) / 1000.0
}

func appendComparisonCSV(path string, r Result) error {
	_, statErr := os.Stat(path)
	writeHeader := os.IsNotExist(statErr)

	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()

	w := csv.NewWriter(f)
	if writeHeader {
		w.Write([]string{
			"experiment", "users", "duration_s",
			"successful", "failed", "total",
			"throughput_rps", "dropout_percent",
			"p50_ms", "p95_ms", "p99_ms",
		})
	}
	w.Write([]string{
		r.Experiment,
		strconv.Itoa(r.Users),
		fmt.Sprintf("%.1f", r.DurationSec),
		strconv.FormatInt(r.Successful, 10),
		strconv.FormatInt(r.Failed, 10),
		strconv.FormatInt(r.Total, 10),
		fmt.Sprintf("%.2f", r.ThroughputRPS),
		fmt.Sprintf("%.2f", r.DropoutPercent),
		fmt.Sprintf("%.2f", r.P50Ms),
		fmt.Sprintf("%.2f", r.P95Ms),
		fmt.Sprintf("%.2f", r.P99Ms),
	})
	w.Flush()
	return w.Error()
}

// ─── Virtual User Worker ──────────────────────────────────────────────────────

func userWorker(ctx context.Context, userID int, cfg Config,
	succ, fail *atomic.Int64, latMu *sync.Mutex, lats *[]time.Duration) {
	client := &http.Client{Timeout: 5 * time.Second}
	rng := rand.New(rand.NewSource(time.Now().UnixNano() + int64(userID)))
	name := fmt.Sprintf("user%d", userID)

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		// Decide: read or write?
		var (
			lat time.Duration
			err error
		)
		if rng.Float64() < cfg.ReadRatio {
			lat, err = doGet(client, cfg.URL+"/feed")
		} else {
			msgLen := cfg.MinMsgLen
			if diff := cfg.MaxMsgLen - cfg.MinMsgLen; diff > 0 {
				msgLen += rng.Intn(diff + 1)
			}
			msg := randomString(rng, msgLen)
			msgID := uuid.New().String()
			lat, err = doPost(client, cfg.URL+"/message", name, msg, msgID)
		}

		if err != nil {
			fail.Add(1)
		} else {
			succ.Add(1)
			latMu.Lock()
			*lats = append(*lats, lat)
			latMu.Unlock()
		}

		// Random sleep between ops
		sleepMs := cfg.MinIntervalMs
		if diff := cfg.MaxIntervalMs - cfg.MinIntervalMs; diff > 0 {
			sleepMs += rng.Intn(diff + 1)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Duration(sleepMs) * time.Millisecond):
		}
	}
}

// ─── Main ─────────────────────────────────────────────────────────────────────

func main() {
	urlFlag := flag.String("url", "http://localhost:4273", "Load balancer URL")
	usersFlag := flag.Int("users", 10, "Number of concurrent virtual users")
	durationFlag := flag.Duration("duration", 60*time.Second, "How long to run")
	minIntervalFlag := flag.Duration("min-interval", 100*time.Millisecond, "Min sleep between messages per user")
	maxIntervalFlag := flag.Duration("max-interval", 1000*time.Millisecond, "Max sleep between messages per user")
	minMsgLenFlag := flag.Int("min-msg-len", 10, "Min random message length in chars")
	maxMsgLenFlag := flag.Int("max-msg-len", 200, "Max random message length in chars")
	readRatioFlag := flag.Float64("read-ratio", 0.3, "Fraction of ops that are GET /feed")
	experimentFlag := flag.String("experiment", "run1", "Label for output files")
	outFlag := flag.String("out", "results", "Output directory")
	rampSecondsFlag := flag.Int("ramp-seconds", 0, "Ramp up from 1 to -users over this many seconds")

	flag.Parse()

	cfg := Config{
		URL:           *urlFlag,
		Users:         *usersFlag,
		Duration:      *durationFlag,
		MinIntervalMs: int(minIntervalFlag.Milliseconds()),
		MaxIntervalMs: int(maxIntervalFlag.Milliseconds()),
		MinMsgLen:     *minMsgLenFlag,
		MaxMsgLen:     *maxMsgLenFlag,
		ReadRatio:     *readRatioFlag,
		Experiment:    *experimentFlag,
		OutDir:        *outFlag,
		RampSeconds:   *rampSecondsFlag,
	}

	if err := os.MkdirAll(cfg.OutDir, 0755); err != nil {
		log.Fatalf("cannot create output dir: %v", err)
	}

	fmt.Printf("\n🚀 Starting Load Generator\n")
	fmt.Printf("  URL           : %s\n", cfg.URL)
	fmt.Printf("  Users         : %d\n", cfg.Users)
	fmt.Printf("  Duration      : %s\n", cfg.Duration)
	fmt.Printf("  Interval      : %dms - %dms\n", cfg.MinIntervalMs, cfg.MaxIntervalMs)
	fmt.Printf("  Msg Length    : %d - %d chars\n", cfg.MinMsgLen, cfg.MaxMsgLen)
	fmt.Printf("  Read Ratio    : %.2f\n", cfg.ReadRatio)
	fmt.Printf("  Ramp-up       : %ds\n", cfg.RampSeconds)
	fmt.Printf("  Experiment    : %s\n", cfg.Experiment)
	fmt.Printf("  Output Dir    : %s\n\n", cfg.OutDir)

	ctx, cancel := context.WithTimeout(context.Background(), cfg.Duration)
	defer cancel()

	var (
		succ      atomic.Int64
		fail      atomic.Int64
		latMu     sync.Mutex
		latencies []time.Duration

		samplesMu sync.Mutex
		samples   []PerSecondSample
	)

	// Sampler ticker: samples throughput and error rate every second
	sampleTicker := time.NewTicker(1 * time.Second)
	defer sampleTicker.Stop()

	go func() {
		second := 0
		var lastSucc, lastFail int64
		for {
			select {
			case <-ctx.Done():
				return
			case <-sampleTicker.C:
				second++
				curSucc := succ.Load()
				curFail := fail.Load()
				dSucc := curSucc - lastSucc
				dFail := curFail - lastFail
				lastSucc = curSucc
				lastFail = curFail

				dTotal := dSucc + dFail
				rps := float64(dTotal)
				errRate := 0.0
				if dTotal > 0 {
					errRate = (float64(dFail) / float64(dTotal)) * 100.0
				}
				samplesMu.Lock()
				samples = append(samples, PerSecondSample{
					Second:  second,
					RPS:     rps,
					ErrRate: errRate,
				})
				samplesMu.Unlock()
			}
		}
	}()

	start := time.Now()

	// Virtual users execution with optional ramp-up
	var wg sync.WaitGroup
	if cfg.RampSeconds > 0 && cfg.Users > 1 {
		rampInterval := time.Duration(float64(cfg.RampSeconds) / float64(cfg.Users) * float64(time.Second))
		for i := 0; i < cfg.Users; i++ {
			wg.Add(1)
			go func(uid int) {
				defer wg.Done()
				userWorker(ctx, uid, cfg, &succ, &fail, &latMu, &latencies)
			}(i)

			if i < cfg.Users-1 {
				select {
				case <-ctx.Done():
					break
				case <-time.After(rampInterval):
				}
			}
		}
	} else {
		for i := 0; i < cfg.Users; i++ {
			wg.Add(1)
			go func(uid int) {
				defer wg.Done()
				userWorker(ctx, uid, cfg, &succ, &fail, &latMu, &latencies)
			}(i)
		}
	}

	wg.Wait()
	elapsed := time.Since(start)

	totalSucc := succ.Load()
	totalFail := fail.Load()
	totalReqs := totalSucc + totalFail

	var throughputRPS float64
	if elapsed.Seconds() > 0 {
		throughputRPS = float64(totalSucc) / elapsed.Seconds()
	}

	var dropoutPercent float64
	if totalReqs > 0 {
		dropoutPercent = (float64(totalFail) / float64(totalReqs)) * 100.0
	}

	// Sort latencies for percentiles
	latMu.Lock()
	sort.Slice(latencies, func(i, j int) bool {
		return latencies[i] < latencies[j]
	})
	p50 := percentileMs(latencies, 50)
	p95 := percentileMs(latencies, 95)
	p99 := percentileMs(latencies, 99)
	latMu.Unlock()

	result := Result{
		Experiment:     cfg.Experiment,
		Users:          cfg.Users,
		DurationSec:    elapsed.Seconds(),
		Successful:     totalSucc,
		Failed:         totalFail,
		Total:          totalReqs,
		ThroughputRPS:  throughputRPS,
		DropoutPercent: dropoutPercent,
		P50Ms:          p50,
		P95Ms:          p95,
		P99Ms:          p99,
	}

	// Print summary
	fmt.Printf("🏁 Experiment Completed: %s\n", result.Experiment)
	fmt.Printf("  Duration        : %.2fs\n", result.DurationSec)
	fmt.Printf("  Virtual Users   : %d\n", result.Users)
	fmt.Printf("  Successful      : %d\n", result.Successful)
	fmt.Printf("  Failed          : %d\n", result.Failed)
	fmt.Printf("  Total Requests  : %d\n", result.Total)
	fmt.Printf("  Throughput (RPS): %.2f\n", result.ThroughputRPS)
	fmt.Printf("  Dropout Rate    : %.2f%%\n", result.DropoutPercent)
	fmt.Printf("  p50 Latency     : %.2f ms\n", result.P50Ms)
	fmt.Printf("  p95 Latency     : %.2f ms\n", result.P95Ms)
	fmt.Printf("  p99 Latency     : %.2f ms\n\n", result.P99Ms)

	// 1. Write {experiment}.json
	jsonPath := filepath.Join(cfg.OutDir, cfg.Experiment+".json")
	if jf, err := os.Create(jsonPath); err != nil {
		log.Printf("warning: could not write JSON: %v", err)
	} else {
		enc := json.NewEncoder(jf)
		enc.SetIndent("", "  ")
		enc.Encode(result)
		jf.Close()
		fmt.Printf("📄 Aggregate JSON saved    -> %s\n", jsonPath)
	}

	// 2. Write {experiment}_timeseries.csv
	tsPath := filepath.Join(cfg.OutDir, cfg.Experiment+"_timeseries.csv")
	if tf, err := os.Create(tsPath); err != nil {
		log.Printf("warning: could not write timeseries CSV: %v", err)
	} else {
		w := csv.NewWriter(tf)
		w.Write([]string{"second", "rps", "err_rate"})
		samplesMu.Lock()
		for _, s := range samples {
			w.Write([]string{
				strconv.Itoa(s.Second),
				fmt.Sprintf("%.2f", s.RPS),
				fmt.Sprintf("%.2f", s.ErrRate),
			})
		}
		samplesMu.Unlock()
		w.Flush()
		tf.Close()
		fmt.Printf("📈 Timeseries CSV saved    -> %s\n", tsPath)
	}

	// 3. Write {experiment}_latencies.csv
	latPath := filepath.Join(cfg.OutDir, cfg.Experiment+"_latencies.csv")
	if lf, err := os.Create(latPath); err != nil {
		log.Printf("warning: could not write latencies CSV: %v", err)
	} else {
		w := csv.NewWriter(lf)
		w.Write([]string{"latency_ms"})
		latMu.Lock()
		for _, l := range latencies {
			w.Write([]string{fmt.Sprintf("%.3f", float64(l.Microseconds())/1000.0)})
		}
		latMu.Unlock()
		w.Flush()
		lf.Close()
		fmt.Printf("📊 Latencies CDF CSV saved -> %s\n", latPath)
	}

	// 4. Write/Append comparison.csv
	cmpPath := filepath.Join(cfg.OutDir, "comparison.csv")
	if err := appendComparisonCSV(cmpPath, result); err != nil {
		log.Printf("warning: could not update comparison CSV: %v", err)
	} else {
		fmt.Printf("📋 Comparison CSV updated  -> %s\n", cmpPath)
	}
}
