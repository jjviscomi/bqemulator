#!/usr/bin/env bash
# Runnable bq-CLI quickstart against an ephemeral bqemulator instance.
# Drives the five canonical commands documented in README.md and asserts
# the final query returns the expected row count. CI runs this via
# ``make test`` so the example does not rot.

set -euo pipefail

# --- prereq checks --------------------------------------------------------

if ! command -v bqemulator >/dev/null 2>&1; then
    echo "ERROR: bqemulator not on PATH. Install via 'pip install bqemulator'." >&2
    exit 1
fi

if ! command -v bq >/dev/null 2>&1; then
    echo "ERROR: bq CLI not on PATH. Install the gcloud SDK:" >&2
    echo "  https://docs.cloud.google.com/sdk/docs/install" >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl required for /healthz polling." >&2
    exit 1
fi

# --- per-run isolated bq config ------------------------------------------

WORK_DIR="$(mktemp -d -t bq-cli-quickstart.XXXXXX)"
NDJSON_FILE="$WORK_DIR/customers.ndjson"
CLOUDSDK_CONFIG="$WORK_DIR/cloudsdk-config"
mkdir -p "$CLOUDSDK_CONFIG"
export CLOUDSDK_CONFIG
export CLOUDSDK_AUTH_DISABLE_CREDENTIALS=true

# --- start ephemeral emulator --------------------------------------------

# Use a fixed dev port to keep the script simple; let the user override.
PORT="${BQEMU_PORT:-9051}"
EMU="http://localhost:$PORT"

# Launch in the background and capture pid for teardown.
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

# Poll /healthz up to 60 seconds.
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

# --- the five-command sequence -------------------------------------------

PROJECT="demo"
DATASET="demo_ds"
TABLE="customers"
TBL_FQ="${PROJECT}:${DATASET}.${TABLE}"
TBL_SQL="${PROJECT}.${DATASET}.${TABLE}"

run_bq() {
    bq --api="$EMU" --project_id="$PROJECT" "$@"
}

# 1) Dataset.
run_bq mk --dataset --location=US "${PROJECT}:${DATASET}"

# 2) Table.
run_bq mk --table "$TBL_FQ" id:INTEGER,name:STRING,email:STRING

# 3) Load NDJSON.
cat > "$NDJSON_FILE" <<EOF
{"id": 1, "name": "Alice", "email": "alice@example.test"}
{"id": 2, "name": "Bob", "email": "bob@example.test"}
{"id": 3, "name": "Carol", "email": "carol@example.test"}
EOF
run_bq load \
    --source_format=NEWLINE_DELIMITED_JSON \
    "$TBL_FQ" "$NDJSON_FILE"

# 4) Query.
RESULT_JSON="$(run_bq query \
    --use_legacy_sql=false --format=json \
    "SELECT COUNT(*) AS n FROM \`${TBL_SQL}\`")"
echo "Query result: $RESULT_JSON"

# Verify count is 3.
if ! echo "$RESULT_JSON" | grep -q '"n": "3"'; then
    echo "ERROR: expected n=3 in query result, got: $RESULT_JSON" >&2
    exit 1
fi

# 5) Cleanup.
run_bq rm -r -f -d "${PROJECT}:${DATASET}"

echo "OK: bq-cli-quickstart completed (5 commands, 3 rows round-tripped)"
