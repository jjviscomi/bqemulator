# ADR 0008: Materialized-snapshot Storage Read API (no MVCC)

- **Status**: Accepted

## Context

BigQuery's Storage Read API supports reading a consistent snapshot at a
specific `snapshot_time`. A production implementation would use MVCC.
DuckDB does not expose MVCC.

## Decision

`CreateReadSession` materializes the projection+filter query result as a
`pyarrow.Table`. Subsequent `ReadRows` calls stream slices of that
materialized table. Writes after session creation do not affect
in-flight sessions — the materialization *is* the snapshot.

## Consequences

- **Positive**: dead simple; leverages DuckDB's columnar output and
  pyarrow's streaming IPC.
- **Positive**: a natural isolation boundary.
- **Negative**: large result sets live in memory for the session lifetime.
  Not a concern for emulator workloads but documented.
