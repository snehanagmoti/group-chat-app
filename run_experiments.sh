#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

BACKEND_1_URL="${BACKEND_1_URL:-https://172.17.0.39:5000}"
BACKEND_2_URL="${BACKEND_2_URL:-https://172.17.0.40:5000}"
BACKEND_3_URL="${BACKEND_3_URL:-https://172.17.0.41:5000}"
LB_PORT="${LB_PORT:-4000}"
LB_URL="${LB_URL:-https://127.0.0.1:${LB_PORT}}"
TLS_CERT="${TLS_CERT:-cert.pem}"
TLS_KEY="${TLS_KEY:-key.pem}"
REQUESTS="${REQUESTS:-5000}"
CONCURRENCY="${CONCURRENCY:-200}"
DELAY="${DELAY:-100ms}"
CLIENT_TIMEOUT="${CLIENT_TIMEOUT:-3s}"
BACKEND_TIMEOUT="${BACKEND_TIMEOUT:-3s}"

LB_PID=""

cleanup() {
    if [[ -n "${LB_PID}" ]] && kill -0 "${LB_PID}" 2>/dev/null; then
        kill "${LB_PID}" 2>/dev/null || true
        wait "${LB_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

require_file() {
    if [[ ! -f "$1" ]]; then
        echo "Required file is missing: $1" >&2
        exit 1
    fi
}

port_is_busy() {
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | grep -qE "[:.]${LB_PORT}[[:space:]]"
        return
    fi
    if command -v netstat >/dev/null 2>&1; then
        netstat -tln 2>/dev/null | grep -qE "[:.]${LB_PORT}[[:space:]]"
        return
    fi
    return 1
}

stop_load_balancer() {
    if [[ -n "${LB_PID}" ]] && kill -0 "${LB_PID}" 2>/dev/null; then
        kill "${LB_PID}"
        wait "${LB_PID}" 2>/dev/null || true
    fi
    LB_PID=""
}

start_load_balancer() {
    local backends="$1"
    if port_is_busy; then
        echo "Port ${LB_PORT} is already in use; stop the existing service first." >&2
        exit 1
    fi

    ./load_balancer \
        -backends "${backends}" \
        -port "${LB_PORT}" \
        -backend-timeout "${BACKEND_TIMEOUT}" \
        -backend-insecure-skip-verify \
        -tls-cert "${TLS_CERT}" \
        -tls-key "${TLS_KEY}" \
        > lb.log 2>&1 &
    LB_PID=$!

    for _ in {1..30}; do
        if curl -k --fail --silent --show-error --max-time 2 "${LB_URL}/lb/health" >/dev/null 2>&1; then
            return
        fi
        if ! kill -0 "${LB_PID}" 2>/dev/null; then
            echo "Load balancer exited during startup:" >&2
            cat lb.log >&2
            exit 1
        fi
        sleep 1
    done
    echo "Load balancer did not become healthy within 30 seconds." >&2
    cat lb.log >&2
    exit 1
}

run_experiment() {
    local name="$1"
    local backends="$2"

    echo
    echo "=== ${name}: ${backends} ==="
    start_load_balancer "${backends}"

    echo "Load balancer status:"
    curl -k --fail --silent --show-error "${LB_URL}/lb/status" | tee "${name}_lb_status.json"
    echo

    ./load_generator \
        -url "${LB_URL}/?delay=${DELAY}" \
        -requests "${REQUESTS}" \
        -concurrency "${CONCURRENCY}" \
        -timeout "${CLIENT_TIMEOUT}" \
        -experiment "${name}" \
        -out "${name}.json" \
        -csv results.csv \
        -insecure

    echo "Load balancer metrics:"
    curl -k --fail --silent --show-error "${LB_URL}/lb/metrics" | tee "${name}_lb_metrics.json"
    echo
    stop_load_balancer
}

require_file "${TLS_CERT}"
require_file "${TLS_KEY}"
rm -f -- results.csv 1_backend.json 3_backends.json \
    1_backend_lb_status.json 1_backend_lb_metrics.json \
    3_backends_lb_status.json 3_backends_lb_metrics.json

echo "=== Building Go commands ==="
go build -o load_balancer ./cmd/load-balancer
go build -o load_generator ./cmd/load-generator

run_experiment "1_backend" "${BACKEND_1_URL}"
run_experiment "3_backends" "${BACKEND_1_URL},${BACKEND_2_URL},${BACKEND_3_URL}"

echo
echo "=== Fair comparison (same requests, concurrency, delay, and timeout) ==="
cat results.csv
