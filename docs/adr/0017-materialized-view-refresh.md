# ADR 0017: Materialized-view refresh — event-driven staleness + lazy recompute

- **Status**: Accepted

## Context

BigQuery materialized views auto-refresh: once a base table changes, a
subsequent `SELECT` against the view returns the up-to-date answer,
either by consulting freshly materialised data or by querying the base
tables with delta logic. The real service hides the refresh cost
behind opaque scheduling; the emulator has to choose one concrete model.

Three options were considered:

1. **Always query live** — rewrite `SELECT * FROM mv` to
   `SELECT (view query)`. Rejected: fails the ship criterion — the view
   must appear materialised, and `INFORMATION_SCHEMA.MATERIALIZED_VIEWS`
   needs a concrete `last_refresh_time` to return.
2. **Eager refresh on every base-table write** — every DML against a
   base table immediately recomputes all dependent MVs. Rejected:
   unpredictable write latencies; a single INSERT can now pay for any
   number of MV recomputes.
3. **Event-driven staleness + lazy recompute on read** (selected).
   Each MV subscribes to `TableDataChanged` events for its base tables.
   The event handler flips `is_stale = True` in the catalog. The next
   read against the MV consults the flag — if stale, the MV is
   re-materialised in place (`CREATE OR REPLACE TABLE mv AS query`)
   under the write lock; otherwise the stored rows are returned as-is.
   An explicit `REFRESH MATERIALIZED VIEW mv` forces an immediate
   recompute regardless of staleness.

## Decision

Option 3. The refresh flow:

1. `CREATE MATERIALIZED VIEW mv AS <query>`:
 - Parse `<query>` with SQLGlot in BigQuery dialect.
 - Extract every base-table reference (walk `exp.Table` nodes,
   skipping function-call Anonymous wrappers).
 - Run the query through the full translation pipeline and
   materialise via `CREATE TABLE project__dataset.mv AS <duckdb_sql>`.
 - Record a `TableMeta` row with `table_type="MATERIALIZED_VIEW"`
   and a `MaterializedViewMeta` row with the query, base-table
   dependencies, `last_refresh_time = now`, `is_stale = False`.

2. `TableDataChanged(project, dataset, table)`:
 - Look up every MV whose dependency set contains that base table.
 - Set `is_stale = True` in the catalog.

3. `SELECT... FROM mv`:
 - If `is_stale` is `True`, take the write lock and run
   `CREATE OR REPLACE TABLE project__dataset.mv AS <duckdb_sql>`,
   then set `is_stale = False`, `last_refresh_time = now`.
 - Stream the materialised rows.

4. `REFRESH MATERIALIZED VIEW mv`:
 - Force-run the refresh path above regardless of the flag.

5. `INFORMATION_SCHEMA.MATERIALIZED_VIEWS`:
 - Emit a `VALUES` subquery shaped like BigQuery's published schema:
   `table_catalog, table_schema, table_name, last_refresh_time,
   refresh_watermark, enable_refresh, refresh_interval_minutes,
   last_modified_time`.
 - `refresh_watermark` equals `last_refresh_time` for the emulator —
   we do not support incremental watermarking.

### Dependency rediscovery on startup

The composition root rebuilds the MV event subscriptions during
startup: every `MaterializedViewMeta` row in the catalog yields a
fresh `EventBus.subscribe(TableDataChanged, handler)` call. The
in-memory subscriptions are thus authoritative for the live process
without leaking into persistence.

### Out of scope

- **Incremental refresh.** The emulator always recomputes in full.
  Real BigQuery supports `CLUSTER BY`, `PARTITION BY`, and partial
  refresh of matched partitions. Documented as out-of-scope in
  `docs/reference/out-of-scope.md`.
- **Allowed non-deterministic inputs.** Views whose query references
  `CURRENT_TIMESTAMP`, `SESSION_USER`, or `RAND` still refresh, but
  their stored rows reflect the refresh-time evaluation — identical
  to BigQuery's behaviour.

## Consequences

- **Positive**: reads are O(1) when the view is fresh; refresh cost is
  paid at most once per base-table change cluster.
- **Positive**: the refresh path is a single `CREATE OR REPLACE TABLE`
  — no schema-evolution corner cases.
- **Positive**: the event subscription path is the same mechanism
  used by the query cache, so there is one place to reason about
  invalidation.
- **Negative**: base-table writes and first-reads-after-write pay a
  refresh cost that can be visible in latency metrics. For the
  emulator this is acceptable; the guide calls out the trade-off.
- **Negative**: a refresh that errors out leaves the old materialised
  data in place with `is_stale = True`. Subsequent reads will keep
  retrying. This is the correct behaviour for a "broken view" state
  but requires the user to fix the base query; we surface the error
  via the REST response and log the failure.
