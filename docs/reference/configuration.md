# Configuration

Every setting can be provided three ways (priority high → low):

1. CLI flag on `bqemulator start …`
2. Environment variable `BQEMU_*`
3. Built-in default

## Network

| Env var | Flag | Default | Description |
|---|---|---|---|
| `BQEMU_REST_HOST` | `--rest-host` | `127.0.0.1` | REST bind host |
| `BQEMU_REST_PORT` | `--rest-port` | `9050` | REST port (`0` for random) |
| `BQEMU_GRPC_HOST` | `--grpc-host` | `127.0.0.1` | gRPC bind host |
| `BQEMU_GRPC_PORT` | `--grpc-port` | `9060` | gRPC port (`0` for random) |

## Persistence

| Env var | Flag | Default | Description |
|---|---|---|---|
| `BQEMU_PERSISTENCE_MODE` | `--ephemeral` / `--persistent` | `ephemeral` | `ephemeral` / `persistent` / `import` |
| `BQEMU_DATA_DIR` | `--data-dir` | unset | Directory for DuckDB file and snapshots (required for persistent) |

## Emulation

| Env var | Flag | Default | Description |
|---|---|---|---|
| `BQEMU_DEFAULT_PROJECT_ID` | `--project` | `test-project` | Project id used when a request omits it |
| `BQEMU_GCS_LOCAL_ROOT` | — | unset | Local directory that `gs://` URIs resolve under |
| `BQEMU_MAX_CONCURRENT_JOBS` | — | `8` | Concurrency cap on query/load/extract/copy jobs |
| `BQEMU_QUERY_CACHE_TTL_SECONDS` | — | `86400` | Query result cache TTL (0 disables) |
| `BQEMU_TIME_TRAVEL_RETENTION_DAYS` | — | `7` | Snapshot retention for time travel (0–90) |

## Observability

| Env var | Flag | Default | Description |
|---|---|---|---|
| `BQEMU_LOG_LEVEL` | `--log-level` | `info` | `trace`/`debug`/`info`/`warning`/`error`/`critical` |
| `BQEMU_LOG_FORMAT` | `--log-format` | `json` | `json` (prod) or `console` (dev) |
| `BQEMU_METRICS_ENABLED` | — | `true` | Expose `/metrics` |
| `BQEMU_TRACING_ENABLED` | — | `false` | Enable OpenTelemetry |
| `BQEMU_OTLP_ENDPOINT` | — | unset | OTLP gRPC endpoint (implies tracing enabled) |
| `BQEMU_ADMIN_ENABLED` | `--enable-admin` | `false` | Expose `/admin/*` debugging endpoints |
