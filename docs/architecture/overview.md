# Architecture overview

bqemulator is a single Python process that serves two protocol surfaces
(REST + gRPC) against one shared data engine (DuckDB).

## High-level diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                       bqemulator process                         │
│                                                                  │
│   ┌────────────────┐            ┌───────────────────────────┐    │
│   │  FastAPI       │            │  grpc.aio                 │    │
│   │  (uvicorn)     │            │                           │    │
│   │                │            │  BigQueryRead             │    │
│   │  /bigquery/v2  │            │  BigQueryWrite            │    │
│   │  /healthz      │            │  grpc.health              │    │
│   │  /readyz       │            │                           │    │
│   │  /metrics      │            │                           │    │
│   └────────┬───────┘            └─────────┬─────────────────┘    │
│            │                              │                      │
│            └──────────────┬───────────────┘                      │
│                           │                                      │
│                 ┌─────────▼────────┐                             │
│                 │   AppContext     │                             │
│                 │  (composition    │                             │
│                 │   root output)   │                             │
│                 └────────┬─────────┘                             │
│                          │                                       │
│  ┌───────────┬───────────┼───────────┬────────────┐              │
│  │           │           │           │            │              │
│ ┌▼──────┐ ┌──▼────┐ ┌────▼────┐ ┌────▼──────┐ ┌───▼──────┐      │
│ │catalog│ │  sql  │ │  jobs   │ │  streaming│ │ versioning│     │
│ │       │ │       │ │         │ │           │ │           │     │
│ └──┬────┘ └───┬───┘ └────┬────┘ └─────┬─────┘ └─────┬────┘      │
│    │          │          │            │              │          │
│    └──────────┴──────────┼────────────┴──────────────┘          │
│                          │                                       │
│                 ┌────────▼──────────┐                            │
│                 │   DuckDBEngine    │                            │
│                 │  (single conn +   │                            │
│                 │   asyncio lock)   │                            │
│                 └────────┬──────────┘                            │
│                          │                                       │
│                    ┌─────▼────────┐                              │
│                    │   DuckDB     │                              │
│                    │ (.duckdb or  │                              │
│                    │  :memory:)   │                              │
│                    └──────────────┘                              │
└──────────────────────────────────────────────────────────────────┘
```

## Layers

| Layer | Role | Import rule |
|---|---|---|
| `bqemulator.domain` | Pure types, errors, protocols | No framework imports |
| `bqemulator.catalog` | Metadata storage (Repository pattern) | Domain + storage only |
| `bqemulator.storage` | DuckDB lifecycle and primitives | Domain only |
| `bqemulator.sql` | SQLGlot translation + rules | Domain + storage + catalog |
| `bqemulator.scripting` | Procedural SQL interpreter | Domain + sql + storage |
| `bqemulator.udf` | UDF runtimes (SQL, JS, TVF) | Domain + storage + catalog |
| `bqemulator.jobs` | Query/load/extract/copy/snapshot execution | Domain + sql + storage + catalog |
| `bqemulator.streaming` | Storage Read/Write APIs | Domain + storage + catalog |
| `bqemulator.versioning` | Snapshots, time travel, materialized views | Domain + storage + catalog |
| `bqemulator.types` | GEOGRAPHY, RANGE, INTERVAL handling | Domain + storage |
| `bqemulator.api` | FastAPI REST adapter | All layers above |
| `bqemulator.grpc_api` | grpc.aio adapter | All layers above |
| `bqemulator.observability` | Logging, metrics, tracing | Settings + FastAPI |
| `bqemulator.server` | Composition root | Everything |

Imports flow **downward only**. Domain modules never import from adapters.

## Request flow (REST query example)

1. Client POSTs to `/bigquery/v2/projects/{p}/queries`.
2. `CorrelationIdMiddleware` binds a request id into the logging context.
3. `AccessLogMiddleware` records a log entry with timing.
4. `MetricsMiddleware` starts a histogram timer.
5. FastAPI routes the request to a handler in `api/routes/jobs.py`.
6. Handler constructs a `QueryJobCommand` and dispatches via
   `jobs/executor.py`.
7. Executor invokes `SQLTranslator.translate()`, then `DuckDBEngine.fetch_arrow()`.
8. Result is converted to BigQuery REST JSON via `storage/arrow_bridge.py`.
9. Response flows back up; middleware records final status and timing.

## Key invariants

- **Single DuckDB connection.** All reads and writes go through
  `DuckDBEngine`. Writes serialize on `asyncio.Lock`.
- **Immutable domain models.** Every catalog entity is a frozen Pydantic
  model; mutation goes through `.model_copy(update=...)`.
- **UTC-only timestamps.** DuckDB connection is opened with
  `SET TimeZone = 'UTC'`; every TIMESTAMP round-trip is UTC.
- **Errors map to BigQuery ErrorProto.** Every `DomainError` subclass has
  an HTTP status, BQ `reason`, and gRPC canonical status.

See each subsystem's own architecture page for details.
