# Materialized views

bqemulator's materialized-view subsystem follows the model locked in by
[ADR 0017](../adr/0017-materialized-view-refresh.md): event-driven
staleness flagging plus lazy recompute on read.

## Lifecycle

Create, query, refresh, drop:

```sql
-- Materialise the view's rows into a regular DuckDB table.
CREATE MATERIALIZED VIEW sales.daily_totals AS
SELECT DATE(placed_at) AS day, SUM(amount) AS total
FROM sales.orders
GROUP BY day;

-- Read like any other table.
SELECT day, total FROM sales.daily_totals ORDER BY day;

-- Force a recompute even if not stale.
REFRESH MATERIALIZED VIEW sales.daily_totals;
-- BigQuery's documented builtin procedure form is also accepted.
CALL BQ.REFRESH_MATERIALIZED_VIEW('project.sales.daily_totals');

-- Drop the view + its rows.
DROP MATERIALIZED VIEW sales.daily_totals;
```

`tableType` is `MATERIALIZED_VIEW`. Direct DML is rejected:

```sql
-- Rejected: the MV refreshes from its base tables.
INSERT INTO sales.daily_totals VALUES (...);
```

## Refresh model

1. **Create**: the view query is parsed with SQLGlot, base-table
   references are extracted, and the rows are materialised once. The
   catalog records the dependency edges.
2. **Stale flag**: every `TableDataChanged` event published by a write
   path checks the dependency edges and flips `is_stale=True` on every
   dependent MV.
3. **Lazy refresh**: the next read against a stale MV runs
   `CREATE OR REPLACE TABLE … AS <query>` under the engine write lock,
   then resets the stale flag.
4. **Forced refresh**: `REFRESH MATERIALIZED VIEW` (or BigQuery's
   documented `CALL BQ.REFRESH_MATERIALIZED_VIEW('<fqdn>')` builtin
   procedure) runs the same path regardless of staleness.

Concurrent stale-readers collapse onto a single recompute — the second
reader rechecks `is_stale` after acquiring the write lock and skips the
redundant CTAS.

## INFORMATION_SCHEMA.MATERIALIZED_VIEWS

The MV catalog is exposed through the standard
`INFORMATION_SCHEMA.MATERIALIZED_VIEWS` virtual table. Project- and
dataset-qualified forms are both supported:

```sql
SELECT table_name, last_refresh_time, refresh_watermark, is_stale
FROM `my_project`.sales.INFORMATION_SCHEMA.MATERIALIZED_VIEWS;
```

Columns:

| Column | Notes |
|---|---|
| `table_catalog` | Project id of the MV. |
| `table_schema` | Dataset id of the MV. |
| `table_name` | Name of the MV. |
| `last_refresh_time` | Wall-clock time of the most recent refresh. |
| `refresh_watermark` | Equal to `last_refresh_time` (the emulator does not support incremental watermarking). |
| `enable_refresh` | Always `TRUE`. |
| `refresh_interval_minutes` | Reported as `30` for compatibility; not used. |
| `is_stale` | `TRUE` once a base-table change has fired and a refresh has not yet run. |

## Limitations

- Refresh is full recompute — no incremental refresh, no partition
  pruning. Documented in
  [`docs/reference/out-of-scope.md`](../reference/out-of-scope.md).
- Non-deterministic functions (`CURRENT_TIMESTAMP`, `RAND`,
  `SESSION_USER`) reflect their refresh-time evaluation just like in
  real BigQuery.
- Dropping a base table leaves dependent MVs in a "broken" state — the
  next refresh will fail; the stored rows continue to satisfy reads
  until then. Drop the dependent MV explicitly if the base table is
  going away.
