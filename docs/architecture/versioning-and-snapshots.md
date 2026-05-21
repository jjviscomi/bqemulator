# Versioning and snapshots

Implementation in `src/bqemulator/versioning/`. See
[ADR 0009](../adr/0009-snapshot-layer-time-travel.md).

## Snapshot layer

Every DDL/DML against a table triggers a snapshot capture (before the
change) via `snapshots.py`:

```
CREATE TABLE "_bqemulator_snapshots"."<iso_ts>_<proj>_<dataset>_<table>"
AS SELECT * FROM "<proj>__<dataset>"."<table>";
```

The catalog records the snapshot and its `snapshot_time`. Snapshots
older than `time_travel_retention_days` are garbage-collected by a
periodic task.

## Time travel

`time_travel.py` rewrites `FOR SYSTEM_TIME AS OF <ts>` to read from the
most recent snapshot whose `snapshot_time <= <ts>`. If no such snapshot
exists (e.g. outside retention), the query errors with
`OutOfRange: Invalid snapshot time`.

## Explicit snapshots

`CREATE SNAPSHOT TABLE base.ref CLONE src.table` uses the same
snapshot mechanism but marks the result `expires = NULL`, so retention
GC skips it.

## Clones

`clone.py` implements `CREATE TABLE new CLONE src` as a CTAS to a new
schema. Storage is not physically shared (DuckDB has no COW) but the
semantics match.

## Materialized views

`materialized_views.py` maintains a dependency graph in the catalog.
`TableDataChanged` events on a base table trigger `REFRESH` of all MVs
that transitively depend on it. Explicit `REFRESH MATERIALIZED VIEW`
forces immediate recomputation.
