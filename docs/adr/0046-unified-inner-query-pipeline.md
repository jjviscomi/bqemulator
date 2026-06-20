# ADR 0046: Unified Inner-Query Rewrite Pipeline

## Status

Accepted

## Context

A BigQuery `SELECT` reaches DuckDB through one of two execution chains,
chosen by whether the job is a single statement or a multi-statement /
control-flow script:

- **Standalone** (`jobs/executor.py`): `execute_query_job` to
  `_run_query_body` to `_run_single_sql`, which applies the canonical
  rewrite chain before translation.
- **Scripted** (`scripting/interpreter.py`): `_exec_sql` dispatches to
  `_run_query` / `_run_statement_with_params` / `_run_query_with_params`,
  each of which re-implemented the rewrite chain inline.

The two chains drifted. The scripted chain re-implemented row-access
enforcement, `INFORMATION_SCHEMA` expansion, `UNNEST` offset rewriting,
and wildcard-table expansion, but omitted three steps the standalone
chain applied:

1. **Materialized-view refresh** (`refresh_dependent_mvs`): a script that
   read a stale materialized view returned stale rows.
2. **Time-travel resolution** (`rewrite_for_system_time`): `FOR
   SYSTEM_TIME AS OF` inside a script was passed through untranslated, so
   the clause reached DuckDB (which has no such syntax) and the query
   silently returned the wrong rows instead of the historical snapshot.
3. **Schema-annotated translation** (`build_catalog_schema` plus the
   translator's `schema=` argument): scripted SELECTs translated without
   the per-table type snapshot, so type-directed rules such as
   `AvgDecimalRule` did not fire.

This affected the scripted path generally, and the `EXPORT DATA` inner
SELECT specifically, which the interpreter runs through `_run_query`.

## Decision

Extract the canonical chain into a single shared module,
`sql/inner_query.py`, with two functions:

- `refresh_dependent_mvs(project_id, bq_sql, ctx)`: walks the BigQuery
  AST and refreshes any stale materialized view the query reads.
- `rewrite_and_translate_statement(bq_sql, *, project_id, ctx, caller,
  translator)`: runs the full chain in order (materialized-view refresh,
  time-travel resolution, row-access enforcement, `INFORMATION_SCHEMA`
  expansion, `UNNEST` offset rewriting, wildcard-table expansion, and
  schema-annotated BigQuery to DuckDB translation) and returns the
  translated DuckDB SQL.

Both `_run_single_sql` and the three interpreter methods call this shared
function. Concerns that genuinely differ between the two paths stay at
the call site, layered on top of the shared chain:

- **Scripting-specific pre-rewrites** run in the interpreter before the
  shared chain: temp-routine call qualification (`_rewrite_temp_calls`)
  and `@var` to positional-placeholder substitution
  (`_rewrite_vars_to_params`).
- **Table-reference qualification, parameter binding, and execution**
  remain caller-owned. Standalone qualifies refs (`rewrite_table_refs`),
  binds BigQuery named query parameters, and fetches Arrow; the
  interpreter qualifies refs, prepends the positional parameters its
  `@var` and `USING` handling emits, and chooses `fetch_arrow`
  (row-producing) or `execute` (dynamic DDL/DML, with last-statement-wins
  shaping). Keeping qualification at the call site is deliberate: a
  malformed-id `ValidationError` from `rewrite_table_refs` is a
  pre-execution domain error that the caller's `try` reshapes via
  `translate_runtime_error`, whereas a translation failure raised by the
  shared helper (e.g. an unsupported feature) must surface unwrapped as a
  `501`, so it is raised before the caller's `try`.

No RFC accompanies this change: there is no change to public API, SQL
semantics, persistence format, or governance. The observable effect is
that scripted SELECTs now match standalone SELECTs, which is the
documented intent.

## Consequences

- **Positive:** The two execution paths cannot drift again, because there
  is one rewrite chain. Materialized-view reads, `FOR SYSTEM_TIME AS OF`
  time-travel, and type-directed translation rules now behave identically
  whether a SELECT runs standalone or inside a script (including the
  `EXPORT DATA` inner SELECT). A regression suite
  (`tests/integration/test_scripted_inner_query_parity.py`) pins the
  parity directly.
- **Negative / limitations:**
  - Scripting-specific pre-rewrites run before the shared chain, so a
    declared script variable used inside a `FOR SYSTEM_TIME AS OF`
    expression is substituted to a placeholder before time-travel
    resolution and is not evaluated as a timestamp. Literal and
    expression `AS OF` targets (the common case) resolve correctly; a
    variable `AS OF` target was non-functional before this change as well.
  - The shared chain parses the statement for materialized-view and
    time-travel detection; both short-circuit when their markers are
    absent, so the cost is bounded for queries that use neither.
