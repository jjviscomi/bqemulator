#!/usr/bin/env bash
# Smoke test: assert /customers returns three seeded rows.

set -euo pipefail

URL="${SERVICE_URL:-http://localhost:8080/customers}"
# Derive the healthz base from URL — referencing $SERVICE_URL directly
# would trip `set -u` whenever the caller leaves it unset.
BASE="${URL%/customers}"

# Wait up to 30s for the service to come up.
for _ in $(seq 1 60); do
    if curl -sf "${BASE}/healthz" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

RESPONSE="$(curl -sf "$URL")"
COUNT="$(printf '%s' "$RESPONSE" | tr ',' '\n' | grep -c '"id"' || true)"

if [ "$COUNT" -ne 3 ]; then
    echo "ERROR: expected 3 rows, got $COUNT: $RESPONSE" >&2
    exit 1
fi

echo "OK: cloud-run-local served 3 rows from bqemulator"
