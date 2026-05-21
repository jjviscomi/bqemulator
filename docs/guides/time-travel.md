# Time travel and snapshots

bqemulator implements BigQuery's time-travel surface — `FOR SYSTEM_TIME AS OF`,
`CREATE SNAPSHOT TABLE`, `CREATE TABLE … CLONE` — using a parallel-schema
snapshot layer ([ADR 0009](../adr/0009-snapshot-layer-time-travel.md),
[ADR 0016](../adr/0016-snapshot-time-semantics.md)).

## Mental model

Every successful DML or DDL on a base table captures a *post-change*
snapshot in the reserved `_bqemulator_snapshots` DuckDB schema. The
catalog records the `snapshot_time` of each capture. Reads with
`FOR SYSTEM_TIME AS OF T` walk backwards to find the most recent
snapshot whose `snapshot_time <= T` and answer from that copy.

Snapshots older than `BQEMU_TIME_TRAVEL_RETENTION_DAYS` (default 7,
max 90) are garbage-collected by a background task. The retention
window is bounded so storage costs stay predictable. `USER` snapshots
created via `CREATE SNAPSHOT TABLE` never expire under retention.

## Time travel

```sql
-- State of the table 5 minutes ago.
SELECT * FROM sales.orders
FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE);

-- State at a specific instant (UTC).
SELECT * FROM sales.orders
FOR SYSTEM_TIME AS OF TIMESTAMP '2024-04-15 12:00:00';
```

Out-of-window targets raise `OUT_OF_RANGE`:

```text
{
  "error": {
    "code": 400,
    "message": "FOR SYSTEM_TIME AS OF is before the retention window …",
    "status": "OUT_OF_RANGE"
  }
}
```

A target between `now - retention` and the table's first observed write
falls back to the live table — the table hasn't been modified, so the
live state IS the historical state.

## Snapshot tables

A snapshot table is a named, immutable, point-in-time copy that lives
inside a regular dataset and survives the retention window:

```sql
CREATE SNAPSHOT TABLE sales.orders_2026_04_15
CLONE sales.orders;
```

The new table appears in `tables.list` with `tableType=SNAPSHOT`.
Direct DML against a snapshot table is rejected:

```sql
-- Rejected: snapshot tables are immutable.
INSERT INTO sales.orders_2026_04_15 VALUES (...);
```

Drop a snapshot table when you no longer need it:

```sql
DROP SNAPSHOT TABLE sales.orders_2026_04_15;
```

## Clones

A clone is a writable, fully-independent copy of a source table. From
the moment of creation the clone has its own data lineage:

```sql
CREATE TABLE sales.orders_work CLONE sales.orders;

-- Mutations to the clone leave the source untouched.
INSERT INTO sales.orders_work VALUES (...);
```

Clones appear in `tables.list` with `tableType=CLONE`.

## Configuration

| Setting | Env var | Default | Limits |
|---|---|---|---|
| Retention window (days) | `BQEMU_TIME_TRAVEL_RETENTION_DAYS` | `7` | `0`–`90` |

Setting retention to `0` disables time-travel queries (snapshots are
captured and immediately garbage-collected). Setting it higher trades
storage for a wider time-travel window — every DML produces a copy of
the touched table.

## Limitations

- BigQuery's `SYSTEM_TIME AS OF` accepts any expression resolvable to a
  TIMESTAMP. The emulator handles literal timestamps natively and
  delegates non-literal expressions to DuckDB. `pytz` is required at
  the Python layer for the latter (declared as a runtime dependency).
- Snapshot capture is full-table; storage cost is proportional to the
  number of writes × retention. Documented as out-of-scope optimisation
  in [`docs/reference/out-of-scope.md`](../reference/out-of-scope.md).
- `FOR SYSTEM_TIME AS OF` against a `MATERIALIZED_VIEW` is not
  supported — query the underlying base tables instead.
