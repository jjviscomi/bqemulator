# ADR 0001: Use DuckDB as the storage and execution engine

- **Status**: Accepted
- **Supersedes**: none
- **Superseded by**: none

## Context

bqemulator needs an embedded SQL engine that can execute analytical queries
locally with no external dependencies. Candidates considered:

| Engine | Pros | Cons |
|---|---|---|
| DuckDB | Columnar analytical engine; excellent SQL coverage; ARRAY/STRUCT/JSON native; Arrow interop; tier-1 Python bindings; stable on-disk format | Single-writer model; some BQ features absent (GEOGRAPHY without ext) |
| SQLite | Ubiquitous; embedded | Row-oriented; weak analytics; no native ARRAY/STRUCT; goccy's chosen engine and source of its SQL gaps |
| In-memory custom | Maximum fidelity | Enormous implementation burden |
| ClickHouse / Databend | Columnar, analytical | Not embedded; requires a server process |

## Decision

Use DuckDB. It is the only embedded engine that matches BigQuery's
analytical character, and it brings first-class ARRAY/STRUCT/JSON types
that map cleanly to GoogleSQL semantics.

## Consequences

- **Positive**: mature, fast, embedded, no subprocess; Arrow interop makes
  Storage Read API implementation straightforward; spatial extension
  gives us GEOGRAPHY; active development and a stable v1.0+ format.
- **Negative**: a single writer at a time — writes serialize on an asyncio
  lock. Acceptable for emulator workloads; documented characteristic.
- **Negative**: a few BigQuery features (time travel, materialized views,
  partitioning) have no native DuckDB equivalent and must be built as a
  layer on top. Accepted; see ADRs 0006, 0009.
