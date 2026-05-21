#!/usr/bin/env bash
# Spin up the published bqemulator image, run NestJS e2e tests, tear down.

set -euo pipefail

PORT="${BQEMU_PORT:-9050}"
IMAGE="${BQEMU_IMAGE:-ghcr.io/jjviscomi/bqemulator:dev}"
NAME="bqemu-nestjs-$(date +%s)"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker not on PATH." >&2
    exit 1
fi

docker run -d --rm --name "$NAME" \
    -p "${PORT}:9050" -p "$((PORT + 10)):9060" \
    -e BQEMU_REST_HOST=0.0.0.0 \
    -e BQEMU_GRPC_HOST=0.0.0.0 \
    -e BQEMU_ADMIN_ENABLED=1 \
    "$IMAGE" >/dev/null

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

EMU="http://localhost:$PORT"
for _ in $(seq 1 120); do
    if curl -sf "$EMU/healthz" >/dev/null 2>&1; then break; fi
    sleep 0.5
done
if ! curl -sf "$EMU/healthz" >/dev/null 2>&1; then
    echo "ERROR: emulator did not become ready at $EMU" >&2
    docker logs "$NAME" >&2 || true
    exit 1
fi

export BQEMU_REST_URL="$EMU"

npm ci
npm run test:e2e
