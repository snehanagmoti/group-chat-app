package loadgenerator

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func validConfig(target string) Config {
	return Config{
		TargetURL:   target,
		Requests:    12,
		Concurrency: 4,
		Timeout:     time.Second,
		Experiment:  "test",
		CSVFile:     "results.csv",
	}
}

func TestValidateConfigRejectsInvalidInputs(t *testing.T) {
	base := validConfig("http://example.test/")
	tests := []Config{
		func() Config { value := base; value.TargetURL = "localhost"; return value }(),
		func() Config { value := base; value.Requests = 0; return value }(),
		func() Config { value := base; value.Concurrency = 0; return value }(),
		func() Config { value := base; value.Timeout = 0; return value }(),
		func() Config { value := base; value.Experiment = ""; return value }(),
		func() Config { value := base; value.CSVFile = ""; return value }(),
	}
	for index, configuration := range tests {
		if err := validateConfig(configuration); err == nil {
			t.Fatalf("invalid config %d unexpectedly passed", index)
		}
	}
}

func TestRunExperimentCountsEveryAttemptAndAllLatencies(t *testing.T) {
	var attempts atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		attempt := attempts.Add(1)
		time.Sleep(2 * time.Millisecond)
		if attempt%4 == 0 {
			http.Error(response, "synthetic failure", http.StatusServiceUnavailable)
			return
		}
		response.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	configuration := validConfig(server.URL)
	result, err := RunExperiment(configuration, server.Client())
	if err != nil {
		t.Fatalf("run experiment: %v", err)
	}
	if result.Successful != 9 || result.Failed != 3 {
		t.Fatalf("success/failed = %d/%d, want 9/3", result.Successful, result.Failed)
	}
	if result.Successful+result.Failed != int64(configuration.Requests) {
		t.Fatalf("attempt accounting mismatch: %#v", result)
	}
	if result.DropoutPercent != 25 {
		t.Fatalf("dropout = %v, want 25", result.DropoutPercent)
	}
	if result.P50MS <= 0 || result.P95MS < result.P50MS || result.P99MS < result.P95MS {
		t.Fatalf("invalid percentiles: p50=%v p95=%v p99=%v", result.P50MS, result.P95MS, result.P99MS)
	}
}

func TestInsecureTLSClientSupportsLabCertificate(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	configuration := validConfig(server.URL)
	configuration.Requests = 2
	configuration.InsecureTLS = true
	client, transport := newHTTPClient(configuration)
	defer transport.CloseIdleConnections()
	result, err := RunExperiment(configuration, client)
	if err != nil {
		t.Fatalf("run TLS experiment: %v", err)
	}
	if result.Successful != 2 || result.Failed != 0 {
		t.Fatalf("TLS result = %#v", result)
	}
}

func TestOutputWritersProduceMachineReadableFiles(t *testing.T) {
	directory := t.TempDir()
	result := Result{
		Experiment:     "one,backend",
		Requests:       10,
		Concurrency:    2,
		Successful:     9,
		Failed:         1,
		ThroughputRPS:  123.4,
		DropoutPercent: 10,
		P50MS:          1,
		P95MS:          2,
		P99MS:          3,
		ElapsedSeconds: 0.1,
	}
	jsonPath := filepath.Join(directory, "result.json")
	csvPath := filepath.Join(directory, "result.csv")
	if err := writeJSON(jsonPath, result); err != nil {
		t.Fatalf("write JSON: %v", err)
	}
	if err := appendCSV(csvPath, result); err != nil {
		t.Fatalf("write CSV: %v", err)
	}

	jsonPayload, err := os.ReadFile(jsonPath)
	if err != nil {
		t.Fatalf("read JSON: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(jsonPayload, &decoded); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	if decoded["successful"] != float64(9) {
		t.Fatalf("JSON result = %#v", decoded)
	}

	csvPayload, err := os.ReadFile(csvPath)
	if err != nil {
		t.Fatalf("read CSV: %v", err)
	}
	rows, err := csv.NewReader(bytes.NewReader(csvPayload)).ReadAll()
	if err != nil {
		t.Fatalf("decode CSV: %v", err)
	}
	if len(rows) != 2 || rows[1][0] != "one,backend" {
		t.Fatalf("CSV rows = %#v", rows)
	}
}

func TestRunCLIWritesBothOutputs(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	directory := t.TempDir()
	jsonPath := filepath.Join(directory, "cli.json")
	csvPath := filepath.Join(directory, "cli.csv")
	var standardOutput bytes.Buffer
	var standardError bytes.Buffer
	exitCode := RunCLI([]string{
		"-url", server.URL,
		"-requests", "4",
		"-concurrency", "2",
		"-experiment", "cli",
		"-out", jsonPath,
		"-csv", csvPath,
	}, &standardOutput, &standardError)
	if exitCode != 0 {
		t.Fatalf("exit=%d stderr=%s", exitCode, standardError.String())
	}
	if _, err := os.Stat(jsonPath); err != nil {
		t.Fatalf("JSON output missing: %v", err)
	}
	if _, err := os.Stat(csvPath); err != nil {
		t.Fatalf("CSV output missing: %v", err)
	}
}
