# syntax=docker/dockerfile:1.6
#
# bqemulator container image.
#
# Multi-stage build: we install into a wheelhouse in the builder stage and
# copy pre-built wheels into a slim runtime image. The runtime image runs
# as a non-root user, listens on REST 9050 and gRPC 9060, and has no shell
# tools beyond what the Python entry point needs.

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
FROM python:3.14-slim-bookworm@sha256:a70519002c49552ea0a853de47599cf40479b001bd7a624f1112eaf44dcaccc7 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[all]"

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.14-slim-bookworm@sha256:a70519002c49552ea0a853de47599cf40479b001bd7a624f1112eaf44dcaccc7 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    BQEMU_REST_HOST=0.0.0.0 \
    BQEMU_GRPC_HOST=0.0.0.0 \
    BQEMU_REST_PORT=9050 \
    BQEMU_GRPC_PORT=9060 \
    BQEMU_DATA_DIR=/var/lib/bqemulator

LABEL org.opencontainers.image.source="https://github.com/jjviscomi/bqemulator" \
      org.opencontainers.image.title="bqemulator" \
      org.opencontainers.image.description="Local emulator for Google BigQuery" \
      org.opencontainers.image.licenses="Apache-2.0"

# Non-root user
RUN groupadd --system --gid 1000 bqemu \
    && useradd --system --uid 1000 --gid bqemu --create-home --home-dir /home/bqemu bqemu \
    && mkdir -p /var/lib/bqemulator \
    && chown -R bqemu:bqemu /var/lib/bqemulator /home/bqemu

COPY --from=builder /opt/venv /opt/venv

USER bqemu
WORKDIR /home/bqemu

EXPOSE 9050 9060

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import httpx, os, sys; sys.exit(0 if httpx.get(f'http://127.0.0.1:{os.environ.get(\"BQEMU_REST_PORT\", 9050)}/healthz').status_code == 200 else 1)"

ENTRYPOINT ["bqemulator"]
CMD ["start", "--data-dir", "/var/lib/bqemulator"]
