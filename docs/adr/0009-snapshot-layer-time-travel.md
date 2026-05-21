# ADR 0009: Snapshot-layer time travel (parallel schemas)

- **Status**: Accepted

## Context

BigQuery's time travel (`FOR SYSTEM_TIME AS OF`) and table snapshots
(`CREATE SNAPSHOT TABLE`) require versioned storage. DuckDB has no
native versioning.

## Decision

Build a snapshot layer in `bqemulator.versioning`:

- Before each DDL or DML that modifies a table, the engine captures a
  snapshot by `CREATE TABLE _bqemulator_snapshots.<ts>_<table>_<id> AS
  SELECT * FROM...` in a parallel reserved schema.
- The catalog records each snapshot's `snapshot_time` and parent table.
- `FOR SYSTEM_TIME AS OF` is rewritten to `FROM _bqemulator_snapshots.X`
  chosen by the catalog lookup.
- Retention is controlled by `time_travel_retention_days` (default 7).
  A periodic GC task removes snapshots older than retention.

`CREATE SNAPSHOT TABLE` reuses the same mechanism but with an explicit,
user-named snapshot that does not expire.

## Consequences

- **Positive**: straightforward implementation using DuckDB's own
  `CREATE TABLE AS SELECT`.
- **Positive**: explicit retention knob; no unbounded growth.
- **Negative**: storage overhead proportional to write frequency and
  retention window. Documented; configurable.
- **Negative**: not byte-level versioning — snapshot granularity is per
  statement, not per row. Matches BigQuery's user-observable semantics.
