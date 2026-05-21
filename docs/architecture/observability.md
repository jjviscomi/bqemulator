# Observability architecture

Three facets, one module tree at `src/bqemulator/observability/`.

## Logging — structlog

- `logging_.py` configures structlog with a shared processor pipeline.
- JSON renderer in production; pretty console renderer when
  `log_format=console`.
- Correlation id is propagated via a `ContextVar` bound by the
  `CorrelationIdMiddleware` (REST) and `CorrelationInterceptor` (gRPC).

## Metrics — Prometheus

- `metrics.py` declares every metric on a `MetricsRegistry`. The
  composition root creates one registry; adapters read it from
  `AppContext`.
- Exposed at `/metrics` (see `metrics_router`).
- No global registry — tests instantiate their own.

## Tracing — OpenTelemetry

- `tracing.py` configures the OTel tracer provider with an OTLP gRPC
  exporter when `BQEMU_TRACING_ENABLED=true`.
- `opentelemetry-instrumentation-fastapi` and
  `opentelemetry-instrumentation-grpc` instrument the inbound request
  surfaces automatically.
- Custom spans are added around SQL translation, DuckDB execution, UDF
  invocation, and scripting statement dispatch.
- No-op by default — zero overhead when disabled.

## Correlation flow

```
  request in ──▶ middleware/interceptor ──▶ bind cid to ContextVar
                                                    │
                                                    ▼
  handler code logs ──▶ structlog processor pulls cid from ContextVar
                                                    │
                                                    ▼
  JSON: { "event": "...", "correlation_id": "...", ... }
```
