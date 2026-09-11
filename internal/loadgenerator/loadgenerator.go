package loadgenerator

import (
	"crypto/tls"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type Config struct {
	TargetURL   string
	Requests    int
	Concurrency int
	Timeout     time.Duration
	Experiment  string
	OutputFile  string
	CSVFile     string
	InsecureTLS bool
}

type Result struct {
	Experiment     string
	Requests       int
	Concurrency    int
	Successful     int64
	Failed         int64
	ThroughputRPS  float64
	DropoutPercent float64
	P50MS          float64
	P95MS          float64
	P99MS          float64
	ElapsedSeconds float64
}

func validateConfig(configuration Config) error {
	parsed, err := url.ParseRequestURI(configuration.TargetURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return errors.New("-url must be a valid absolute http or https URL")
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return errors.New("-url scheme must be http or https")
	}
	if configuration.Requests <= 0 {
		return errors.New("-requests must be greater than zero")
	}
	if configuration.Concurrency <= 0 {
		return errors.New("-concurrency must be greater than zero")
	}
	if configuration.Timeout <= 0 {
		return errors.New("-timeout must be greater than zero")
	}
	if strings.TrimSpace(configuration.Experiment) == "" {
		return errors.New("-experiment must not be empty")
	}
	if strings.TrimSpace(configuration.CSVFile) == "" {
		return errors.New("-csv must not be empty")
	}
	return nil
}

func newHTTPClient(configuration Config) (*http.Client, *http.Transport) {
	transport := &http.Transport{
		Proxy:               http.ProxyFromEnvironment,
		DialContext:         (&net.Dialer{Timeout: configuration.Timeout, KeepAlive: 30 * time.Second}).DialContext,
		ForceAttemptHTTP2:   true,
		MaxIdleConns:        max(256, configuration.Concurrency*2),
		MaxIdleConnsPerHost: max(256, configuration.Concurrency*2),
		IdleConnTimeout:     90 * time.Second,
		TLSHandshakeTimeout: configuration.Timeout,
		TLSClientConfig: &tls.Config{
			MinVersion:         tls.VersionTLS12,
			InsecureSkipVerify: configuration.InsecureTLS,
		},
	}
	return &http.Client{Timeout: configuration.Timeout, Transport: transport}, transport
}

func RunExperiment(configuration Config, client *http.Client) (Result, error) {
	if err := validateConfig(configuration); err != nil {
		return Result{}, err
	}
	if client == nil {
		return Result{}, errors.New("HTTP client must not be nil")
	}

	workers := min(configuration.Concurrency, configuration.Requests)
	jobs := make(chan struct{})
	latencies := make(chan time.Duration, configuration.Requests)
	var successful atomic.Int64
	var failed atomic.Int64
	var waitGroup sync.WaitGroup

	started := time.Now()
	for worker := 0; worker < workers; worker++ {
		waitGroup.Add(1)
		go func() {
			defer waitGroup.Done()
			for range jobs {
				requestStarted := time.Now()
				request, err := http.NewRequest(http.MethodGet, configuration.TargetURL, nil)
				if err == nil {
					request.Header.Set("User-Agent", "PixelChat-Load-Generator/1.0")
					response, requestError := client.Do(request)
					err = requestError
					if response != nil {
						_, copyError := io.Copy(io.Discard, response.Body)
						closeError := response.Body.Close()
						if err == nil {
							err = copyError
						}
						if err == nil {
							err = closeError
						}
						if err == nil && response.StatusCode >= 200 && response.StatusCode < 300 {
							successful.Add(1)
						} else {
							failed.Add(1)
						}
					} else {
						failed.Add(1)
					}
				} else {
					failed.Add(1)
				}
				latencies <- time.Since(requestStarted)
			}
		}()
	}

	go func() {
		for request := 0; request < configuration.Requests; request++ {
			jobs <- struct{}{}
		}
		close(jobs)
	}()

	waitGroup.Wait()
	close(latencies)
	elapsed := time.Since(started)

	durations := make([]time.Duration, 0, configuration.Requests)
	for latency := range latencies {
		durations = append(durations, latency)
	}
	sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })

	successCount := successful.Load()
	failureCount := failed.Load()
	result := Result{
		Experiment:     configuration.Experiment,
		Requests:       configuration.Requests,
		Concurrency:    configuration.Concurrency,
		Successful:     successCount,
		Failed:         failureCount,
		ThroughputRPS:  float64(successCount) / elapsed.Seconds(),
		DropoutPercent: float64(failureCount) / float64(configuration.Requests) * 100,
		P50MS:          percentileMS(durations, 50),
		P95MS:          percentileMS(durations, 95),
		P99MS:          percentileMS(durations, 99),
		ElapsedSeconds: elapsed.Seconds(),
	}
	return result, nil
}

func percentileMS(sortedDurations []time.Duration, percentile float64) float64 {
	if len(sortedDurations) == 0 {
		return 0
	}
	rank := int(math.Ceil(percentile/100*float64(len(sortedDurations)))) - 1
	rank = max(0, min(rank, len(sortedDurations)-1))
	return float64(sortedDurations[rank]) / float64(time.Millisecond)
}

func resultAsMap(result Result) map[string]any {
	return map[string]any{
		"experiment":      result.Experiment,
		"requests":        result.Requests,
		"concurrency":     result.Concurrency,
		"successful":      result.Successful,
		"failed":          result.Failed,
		"throughput_rps":  result.ThroughputRPS,
		"dropout_percent": result.DropoutPercent,
		"p50_ms":          result.P50MS,
		"p95_ms":          result.P95MS,
		"p99_ms":          result.P99MS,
		"elapsed_seconds": result.ElapsedSeconds,
	}
}

func writeJSON(path string, result Result) error {
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	encodeError := encoder.Encode(resultAsMap(result))
	closeError := file.Close()
	return errors.Join(encodeError, closeError)
}

func appendCSV(path string, result Result) error {
	fileInfo, statError := os.Stat(path)
	isNew := errors.Is(statError, os.ErrNotExist) || (statError == nil && fileInfo.Size() == 0)
	if statError != nil && !errors.Is(statError, os.ErrNotExist) {
		return statError
	}

	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	writer := csv.NewWriter(file)
	if isNew {
		if err := writer.Write([]string{"Experiment", "Success", "Failed", "RPS", "Dropout", "p50", "p95", "p99"}); err != nil {
			_ = file.Close()
			return err
		}
	}
	row := []string{
		result.Experiment,
		strconv.FormatInt(result.Successful, 10),
		strconv.FormatInt(result.Failed, 10),
		fmt.Sprintf("%.1f", result.ThroughputRPS),
		fmt.Sprintf("%.1f%%", result.DropoutPercent),
		fmt.Sprintf("%.1fms", result.P50MS),
		fmt.Sprintf("%.1fms", result.P95MS),
		fmt.Sprintf("%.1fms", result.P99MS),
	}
	writeError := writer.Write(row)
	writer.Flush()
	flushError := writer.Error()
	closeError := file.Close()
	return errors.Join(writeError, flushError, closeError)
}

func defaultJSONPath(experiment string) string {
	var builder strings.Builder
	for _, character := range experiment {
		if (character >= 'a' && character <= 'z') ||
			(character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') ||
			character == '-' || character == '_' {
			builder.WriteRune(character)
		} else {
			builder.WriteRune('_')
		}
	}
	name := strings.Trim(builder.String(), "_")
	if name == "" {
		name = "experiment"
	}
	return filepath.Clean(name + ".json")
}

func RunCLI(arguments []string, standardOutput io.Writer, standardError io.Writer) int {
	flags := flag.NewFlagSet("load-generator", flag.ContinueOnError)
	flags.SetOutput(standardError)

	configuration := Config{}
	flags.StringVar(&configuration.TargetURL, "url", "", "Target URL")
	flags.IntVar(&configuration.Requests, "requests", 1000, "Total request attempts")
	flags.IntVar(&configuration.Concurrency, "concurrency", 10, "Concurrent workers")
	flags.DurationVar(&configuration.Timeout, "timeout", 5*time.Second, "Per-request timeout")
	flags.StringVar(&configuration.Experiment, "experiment", "baseline", "Experiment name")
	flags.StringVar(&configuration.OutputFile, "out", "", "Output JSON file")
	flags.StringVar(&configuration.CSVFile, "csv", "results.csv", "CSV comparison file")
	flags.BoolVar(&configuration.InsecureTLS, "insecure", false, "Accept a self-signed target TLS certificate (lab only)")

	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if err := validateConfig(configuration); err != nil {
		fmt.Fprintf(standardError, "load-generator: %v\n", err)
		return 2
	}

	client, transport := newHTTPClient(configuration)
	defer transport.CloseIdleConnections()
	result, err := RunExperiment(configuration, client)
	if err != nil {
		fmt.Fprintf(standardError, "load-generator: %v\n", err)
		return 1
	}

	outputPath := configuration.OutputFile
	if outputPath == "" {
		outputPath = defaultJSONPath(configuration.Experiment)
	}
	if err := writeJSON(outputPath, result); err != nil {
		fmt.Fprintf(standardError, "load-generator: write %s: %v\n", outputPath, err)
		return 1
	}
	if err := appendCSV(configuration.CSVFile, result); err != nil {
		fmt.Fprintf(standardError, "load-generator: write %s: %v\n", configuration.CSVFile, err)
		return 1
	}

	fmt.Fprintf(
		standardOutput,
		"Experiment %s: %d successful, %d failed, %.1f RPS, %.2f%% dropout, elapsed %s\n",
		result.Experiment,
		result.Successful,
		result.Failed,
		result.ThroughputRPS,
		result.DropoutPercent,
		time.Duration(result.ElapsedSeconds*float64(time.Second)).Round(time.Millisecond),
	)
	return 0
}
