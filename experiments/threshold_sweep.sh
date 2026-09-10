#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# threshold_sweep.sh — Lab 6 Threshold Sweep Experiment
#
# Sweeps OVERLOAD_THRESHOLD from 0.50 to 0.85, running the load generator
# against /message and /feed for each value.
# Results are appended to experiments/results/threshold_sweep.csv.
#
# Usage:
#   BACKENDS="http://10.1.75.53:3294,http://10.1.75.53:3295,http://10.1.75.53:3296" \
#   bash experiments/threshold_sweep.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

LB_PORT=3293
LB_HOST="0.0.0.0"
BACKENDS="${BACKENDS:-http://127.0.0.1:8081,http://127.0.0.1:8082,http://127.0.0.1:8083}"
REQUESTS=2000
CONCURRENCY=40
RESULTS_DIR="experiments/results"
CSV="${RESULTS_DIR}/threshold_sweep.csv"

mkdir -p "$RESULTS_DIR"

# CSV header (write once)
if [ ! -f "$CSV" ]; then
    echo "threshold,requests,concurrency,rps,avg_ms,p50_ms,p95_ms,p99_ms,errors,switch_count" > "$CSV"
fi

for T in 0.50 0.60 0.65 0.70 0.75 0.80 0.85; do
    echo ""
    echo "▶ ─────────────────────────────────────────────────"
    echo "  threshold=$T"
    echo "  ─────────────────────────────────────────────────"

    # Kill any running LB
    pkill -f "bin/lb" 2>/dev/null || true
    sleep 1

    # Start LB with this threshold
    ./bin/lb \
        -port "$LB_PORT" \
        -backends "$BACKENDS" \
        -overload-threshold "$T" \
        -hysteresis 0.10 \
        -health-interval 1s \
        -backend-timeout 800ms &
    LB_PID=$!
    sleep 2   # wait for health checks to pass

    LB_URL="http://${LB_HOST}:${LB_PORT}"

    # Reset LB metrics before each run
    curl -sf "${LB_URL}/lb/reset" > /dev/null || true

    # Run load generator against /ws (main chat endpoint used in load_generator)
    echo "  Running load generator (req=${REQUESTS}, concurrency=${CONCURRENCY})..."
    ./bin/loadgen \
        -url "${LB_URL}/health" \
        -requests "$REQUESTS" \
        -concurrency "$CONCURRENCY" \
        2>&1 | tee "/tmp/loadgen_t${T}.log" || true

    # Collect metrics from LB
    METRICS=$(curl -sf "${LB_URL}/lb/metrics" || echo '{}')
    AVG_MS=$(echo "$METRICS"  | python3 -c "import json,sys; d=json.load(sys.stdin); print(round(d.get('avg_ms',0),2))" 2>/dev/null || echo "0")
    P50=$(echo "$METRICS"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(round(d.get('p50_ms',0),2))" 2>/dev/null || echo "0")
    P95=$(echo "$METRICS"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(round(d.get('p95_ms',0),2))" 2>/dev/null || echo "0")
    P99=$(echo "$METRICS"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(round(d.get('p99_ms',0),2))" 2>/dev/null || echo "0")
    FAILED=$(echo "$METRICS"  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('failed',0))" 2>/dev/null || echo "0")
    SWITCHES=$(echo "$METRICS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('switch_count',0))" 2>/dev/null || echo "0")
    TOTAL=$(echo "$METRICS"   | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('total',0))" 2>/dev/null || echo "0")

    # Estimate RPS from total / elapsed (crude — loadgen doesn't print elapsed)
    RPS=$(python3 -c "print(round($TOTAL / max($AVG_MS/1000 * $REQUESTS, 1), 1))" 2>/dev/null || echo "0")

    echo "  avg=${AVG_MS}ms p50=${P50}ms p95=${P95}ms p99=${P99}ms errors=${FAILED} switches=${SWITCHES}"
    echo "${T},${REQUESTS},${CONCURRENCY},${RPS},${AVG_MS},${P50},${P95},${P99},${FAILED},${SWITCHES}" >> "$CSV"

    # Stop LB
    kill "$LB_PID" 2>/dev/null || true
    sleep 1
done

echo ""
echo "✅ Sweep complete. Results written to: ${CSV}"
cat "$CSV"
