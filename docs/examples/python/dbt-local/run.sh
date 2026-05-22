#!/usr/bin/env bash
# Drives `dbt build` against an ephemeral bqemulator instance.
# CI runs this via `make test` so a regression on the dbt-bigquery
# code path breaks the build.

set -euo pipefail

if ! command -v bqemulator >/dev/null 2>&1; then
    echo "ERROR: bqemulator not on PATH. Install via 'pip install bqemulator'." >&2
    exit 1
fi

if ! command -v dbt >/dev/null 2>&1; then
    echo "ERROR: dbt not on PATH. Install with 'pip install -r requirements.txt'." >&2
    exit 1
fi

WORK_DIR="$(mktemp -d -t bqemu-dbt-local.XXXXXX)"
PORT="${BQEMU_PORT:-9151}"
EMU="http://localhost:$PORT"
# dbt-bigquery 1.9+ forwards ``BIGQUERY_EMULATOR_HOST`` verbatim into
# ``client_options.api_endpoint`` without prepending a scheme.
# ``google-cloud-bigquery`` itself adds ``http://`` when missing, but
# dbt bypasses that branch, so the env var must already carry the
# scheme or ``requests`` aborts with
# ``No connection adapters were found for 'localhost:PORT/...'``.
export BIGQUERY_EMULATOR_HOST="http://localhost:$PORT"
export BQ_PROJECT="${BQ_PROJECT:-bqemu-demo}"

bqemulator start --ephemeral --rest-port "$PORT" >"$WORK_DIR/emulator.log" 2>&1 &
EMU_PID=$!

cleanup() {
    if kill -0 "$EMU_PID" 2>/dev/null; then
        kill "$EMU_PID" 2>/dev/null || true
        wait "$EMU_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

for _ in $(seq 1 120); do
    if curl -sf "$EMU/healthz" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done
if ! curl -sf "$EMU/healthz" >/dev/null 2>&1; then
    echo "ERROR: emulator did not become ready at $EMU within 60s" >&2
    cat "$WORK_DIR/emulator.log" >&2
    exit 1
fi

cd "$(dirname "$0")"

dbt deps --profiles-dir . || true
dbt seed --profiles-dir . --target emulator
dbt run --profiles-dir . --target emulator
dbt test --profiles-dir . --target emulator

echo "OK: dbt build cycle completed against bqemulator"
