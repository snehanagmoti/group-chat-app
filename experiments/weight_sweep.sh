#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# weight_sweep.sh — Experiment to justify scoring weights and EWMA alpha
#
# Runs the load generator at varying weight configurations and records
# actual p99 latency and backend distribution per run. This produces data
# to back the claim that w_cpu=0.40 and w_lat=0.30 are optimal.
#
# Usage (from project root, with all 4 machines running):
#   BACKENDS="http://sys2:3294,http://sys3:3295,http://sys4:3296" \
#   bash experiments/weight_sweep.sh
#
# Output: experiments/results/weight_sweep.csv
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LB_PORT=3293
BACKENDS="${BACKENDS:-http://127.0.0.1:8081,http://127.0.0.1:8082,http://127.0.0.1:8083}"
REQUESTS=3000
CONCURRENCY=40
USERS=50
MIN_MSG=20
MAX_MSG=200
RESULTS_DIR="experiments/results"
CSV="${RESULTS_DIR}/weight_sweep.csv"

mkdir -p "$RESULTS_DIR"

echo "wcpu,wlat,wmem,wact,ewma_alpha,requests,concurrency,rps,p50_ms,p95_ms,p99_ms,errors,switch_count" > "$CSV"

# Weight configurations to sweep
# Format: "wcpu wlat wmem wact"
# Weights must sum to 1.0
CONFIGS=(
  "0.25 0.25 0.25 0.25"   # equal weights (null hypothesis)
  "0.50 0.25 0.15 0.10"   # cpu-heavy
  "0.25 0.50 0.15 0.10"   # latency-heavy
  "0.40 0.30 0.15 0.15"   # our chosen config
  "0.35 0.35 0.15 0.15"   # cpu/lat balanced
  "0.45 0.35 0.10 0.10"   # cpu+lat dominant
  "0.20 0.20 0.30 0.30"   # mem+active dominant (hypothesis: worse)
)

# EWMA alpha values to sweep (after best weights are found)
ALPHAS=("0.1" "0.2" "0.3" "0.4" "0.5")

run_experiment() {
  local wcpu="$1" wlat="$2" wmem="$3" wact="$4" alpha="$5"

  # Kill existing LB
  pkill -f "bin/lb" 2>/dev/null || true
  sleep 1

  ./bin/lb \
    -port        "$LB_PORT" \
    -backends    "$BACKENDS" \
    -wcpu        "$wcpu" \
    -wlat        "$wlat" \
    -wmem        "$wmem" \
    -wact        "$wact" \
    -ewma-alpha  "$alpha" \
    -overload-threshold 0.70 \
    -hysteresis  0.10 \
    -health-interval 1s \
    -backend-timeout 800ms &
  LB_PID=$!
  sleep 2

  LB_URL="http://0.0.0.0:${LB_PORT}"
  curl -sf "${LB_URL}/lb/reset" >/dev/null || true

  echo "  → wcpu=$wcpu wlat=$wlat wmem=$wmem wact=$wact alpha=$alpha"

  ./bin/loadgen \
    -url         "${LB_URL}" \
    -requests    "$REQUESTS" \
    -concurrency "$CONCURRENCY" \
    -users       "$USERS" \
    -min-msg-len "$MIN_MSG" \
    -max-msg-len "$MAX_MSG" \
    -feed-ratio  0.2 \
    -experiment  "w${wcpu}_${wlat}_a${alpha}" \
    -csv         "$CSV" \
    2>&1 | tail -5

  kill "$LB_PID" 2>/dev/null || true
  sleep 1
}

echo ""
echo "▶ Phase 1: Weight configuration sweep (alpha=0.3 fixed)"
for cfg in "${CONFIGS[@]}"; do
  read -r wcpu wlat wmem wact <<< "$cfg"
  run_experiment "$wcpu" "$wlat" "$wmem" "$wact" "0.3"
done

echo ""
echo "▶ Phase 2: EWMA alpha sweep (best weights fixed: 0.40 0.30 0.15 0.15)"
for alpha in "${ALPHAS[@]}"; do
  run_experiment "0.40" "0.30" "0.15" "0.15" "$alpha"
done

echo ""
echo "✅ Weight sweep complete → ${CSV}"
cat "$CSV"
