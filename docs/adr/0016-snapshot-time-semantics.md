# ADR 0016: Snapshot time semantics — POST-change capture, ≤-target lookup

- **Status**: Accepted

## Context

[ADR 0009](0009-snapshot-layer-time-travel.md) locked in a parallel-schema
snapshot layer to power `FOR SYSTEM_TIME AS OF`, `CREATE SNAPSHOT TABLE`,
and `CREATE TABLE... CLONE`. That ADR left the exact *snapshot time*
semantics ambiguous:

> Before each DDL or DML that modifies a table, the engine captures a
> snapshot […] `FOR SYSTEM_TIME AS OF` is rewritten to `FROM
> _bqemulator_snapshots.X` chosen by the catalog lookup.

Two concrete designs match that sentence:

1. **PRE-change capture** with *“smallest snap whose `snapshot_time >
   target`”* lookup. Each snapshot represents the state *before* a
   write; a query at `T` walks forward to the next snapshot, which
   contains the state that was current through `T`.
2. **POST-change capture** with *“largest snap whose `snapshot_time
   ≤ target`”* lookup. Each snapshot represents the state *after* a
   commit; a query at `T` walks backward to the most recent committed
   state, which is the state that was current at `T`.

These are observationally equivalent for well-formed inputs but differ
in edge cases (first snapshot, dropped tables, failed DML), in GC
policy, and in code complexity.

## Decision

Use POST-change semantics:

- The snapshot is captured **after** a DML/DDL commits successfully. A
  failed write captures no snapshot.
- `snapshot_time` is the commit time (the emulator clock at capture).
- The time-travel lookup for target `T` returns the snapshot with the
  *largest* `snapshot_time ≤ T`. If no snapshot satisfies that test:
 * If the table has **no** snapshots at all, the live table is
   returned — the table is unchanged since creation, so the live
   state is the correct answer at any `T` in the retention window.
 * If the table has snapshots but all are strictly greater than `T`,
   the query raises `OutOfRange`. (`T` is before the table's first
   observable state.)

### Validation

- `T > now` → `OutOfRange: snapshot time is in the future`.
- `T < now − BQEMU_TIME_TRAVEL_RETENTION_DAYS` → `OutOfRange:
  snapshot time is beyond retention window`.
- Otherwise the lookup rules above apply.

### Capture points

Capture is triggered from a single helper,
`SnapshotManager.record_change(project, dataset, table)`, which is
called from every write path after the DuckDB commit returns:

- Job executor: DML (INSERT / UPDATE / DELETE / MERGE / TRUNCATE),
  LOAD, COPY.
- REST `tabledata.insertAll`.
- gRPC Storage Write API commits (default, committed, pending-commit,
  buffered-flush).
- REST `DELETE /tables/{t}` (final snapshot before `DROP`, so the prior
  state remains queryable within retention).

`CREATE TABLE`, `ALTER TABLE`, `CREATE ROUTINE`, and similar catalog-only
changes do **not** capture. The base-table state does not change.

### Storage

- Snapshots live in `_bqemulator_snapshots` as
  `CREATE TABLE "_bqemulator_snapshots"."<id>" AS SELECT * FROM source`.
- `<id>` is `s_<nanos>_<hex8>` where `<nanos>` is the capture time in
  nanoseconds and `<hex8>` is a short UUID suffix — sortable, unique,
  and SQL-safe.
- Metadata is stored in `_bqemulator_catalog.snapshots`. Every entry
  carries `snapshot_id`, `source_{project,dataset,table}`,
  `snapshot_time`, `kind` (`AUTO` / `USER`), `expires_at`
  (NULL for USER snapshots used by `CREATE SNAPSHOT TABLE`).

### Garbage collection

A periodic task drops `AUTO` snapshots with `snapshot_time < now −
retention`. The task runs every `ceil(retention_seconds / 24)` seconds
(default: 30 minutes for the 7-day retention) and is driven by
`asyncio.create_task` started from the composition root. GC also runs
once on startup. `USER` snapshots are never dropped by GC — they are
only removed by `DROP SNAPSHOT TABLE`.

## Consequences

- **Positive**: the lookup logic matches BigQuery's documented *as-of*
  semantics (“state that was current at T” is “latest commit ≤ T”).
- **Positive**: snapshots never leak when a DML fails — capture is
  gated on a successful commit.
- **Positive**: the GC policy is simple — filter on `snapshot_time <
  now − retention AND kind = 'AUTO'`.
- **Negative**: two writes within a microsecond collapse to the same
  `snapshot_time`. The emulator's clock is millisecond-granular; tests
  that advance time by zero must bump the clock before the second
  write or the second snapshot overwrites the first entry. Documented
  in the testing utilities.
- **Negative**: we pay a full-table CTAS for every DML. That matches
  DuckDB's ergonomics (no MVCC, no WAL replay) and is fine for
  emulator workloads but would be prohibitive for large production
  tables. The storage-cost warning is called out in the time-travel
  guide.
