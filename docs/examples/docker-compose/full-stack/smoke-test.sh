#!/usr/bin/env bash
# Asserts the four services in the stack are healthy and serving.

set -euo pipefail

# 1. App returns 3 rows.
RESP="$(curl -sf http://localhost:8080/customers)"
COUNT="$(printf '%s' "$RESP" | tr ',' '\n' | grep -c '"id"' || true)"
if [ "$COUNT" -ne 3 ]; then
    echo "ERROR: app /customers returned $COUNT rows: $RESP" >&2
    exit 1
fi
echo "OK: app served 3 rows"

# 2. Prometheus reports bqemulator up.
PROM="$(curl -sf 'http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22bqemulator%22%7D' || true)"
if ! printf '%s' "$PROM" | grep -q '"value":\[\([0-9.]\+\),"1"\]'; then
    echo "ERROR: prometheus does not see bqemulator up: $PROM" >&2
    exit 1
fi
echo "OK: prometheus reports bqemulator up"

# 3. Grafana health endpoint.
HEALTH="$(curl -sf http://localhost:3000/api/health)"
if ! printf '%s' "$HEALTH" | grep -q '"database":[[:space:]]*"ok"'; then
    echo "ERROR: grafana health: $HEALTH" >&2
    exit 1
fi
echo "OK: grafana reports healthy"
