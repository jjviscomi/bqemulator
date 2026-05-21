# Observability

## Logs

bqemulator emits structured JSON logs (via
[structlog](https://www.structlog.org/)). In dev mode, pass
`--log-format console` for pretty colored output.

Every log line carries a `correlation_id` pulled from the request's
`x-correlation-id` header (or auto-generated). Grep by correlation id to
reconstruct a request's trace.

## Metrics

Prometheus-format metrics are served at `/metrics` (same port as REST).

Key metrics:

- `bqemulator_rest_requests_total{method,route,status}` — counter
- `bqemulator_rest_request_latency_seconds{method,route}` — histogram
- `bqemulator_grpc_requests_total{service,method,status}` — counter
- `bqemulator_grpc_request_latency_seconds{service,method}` — histogram
- `bqemulator_jobs_total{type,status}` — counter
- `bqemulator_job_duration_seconds{type}` — histogram
- `bqemulator_sql_translation_total{outcome}` — counter
- `bqemulator_query_cache_hits_total` / `bqemulator_query_cache_misses_total` — counters
- `bqemulator_read_streams_active` / `bqemulator_write_streams_active{stream_type}` — gauges
- `bqemulator_build_info{version}` — gauge (always 1)

## Traces

OpenTelemetry tracing is opt-in. Set `BQEMU_TRACING_ENABLED=true` and
`BQEMU_OTLP_ENDPOINT=localhost:4317` to export spans to a local collector
(e.g. Jaeger, Tempo).
