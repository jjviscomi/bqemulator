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
export BIGQUERY_EMULATOR_HOST="localhost:$PORT"
export BQ_PROJECT="${BQ_PROJECT:-bqemu-demo}"

# dbt-bigquery's connection setup parses a service-account keyfile
# before any API call is made; BIGQUERY_EMULATOR_HOST only kicks in
# afterwards. Synthesise a syntactically valid keyfile (real RSA PEM
# so service_account.Credentials.from_service_account_file passes) at
# runtime so the auth handshake succeeds without leaking a real key
# into the repo. The emulator never calls Google's token endpoint, so
# the key is purely for parser appeasement.
export BQEMU_FAKE_SA_KEY="$WORK_DIR/fake-sa.json"
python - <<PY
import json, pathlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
pathlib.Path("$BQEMU_FAKE_SA_KEY").write_text(json.dumps({
    "type": "service_account",
    "project_id": "$BQ_PROJECT",
    "private_key_id": "bqemu-fake-key",
    "private_key": pem,
    "client_email": "bqemu-fake@${BQ_PROJECT}.iam.gserviceaccount.com",
    "client_id": "1",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url":
        "https://www.googleapis.com/oauth2/v1/certs",
}))
PY

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
