# ADR 0014: UDF materialization strategy — per-runtime dispatch with eager DuckDB registration

- **Status**: Accepted

## Context

Phase 6 introduces three routine runtimes — SQL scalar, table-valued
(TVF), and JavaScript — and a `/routines` REST CRUD API. Each routine
must be invocable from an ordinary SELECT (``SELECT my_udf(col) FROM t``)
and from inside a procedural script (``SET x = my_udf(1); CALL my_proc();``).

Three competing materialization strategies were considered:

1. **Inline rewrite at translation time.** The SQL translator would
   expand every UDF call into its body as a correlated subquery. This
   couples the translator to the catalog, breaks the rule registry's
   pure-AST contract, and doesn't work for recursive UDFs or JS UDFs.
2. **Ad-hoc "on first call" materialization.** Defer DuckDB
   registration until first invocation. Complicates error paths
   (compilation errors surface at query time, not CREATE FUNCTION time)
   and introduces a race against concurrent callers.
3. **Eager per-runtime materialization at CREATE FUNCTION time.** On
   every routine create/update, the corresponding runtime strategy
   registers the function with DuckDB immediately. On delete, it is
   dropped. On server startup, the routines catalog is hydrated and
   every routine is re-registered.

## Decision

Strategy pattern, matching the Phase 5 write-API pattern.

```
udf/
├── runtime.py          # UDFRuntime protocol + UDFRegistry
├── sql_udf.py          # SQL scalar via CREATE MACRO
├── table_valued.py     # TVF via CREATE MACRO ... AS TABLE
├── js_udf.py           # JS scalar via DuckDB create_function + mini-racer
└── types.py            # BigQuery ↔ DuckDB type mapping for routines
```

1. **`UDFRuntime` protocol** exposes `materialize(routine, engine)`
   and `deregister(routine, engine)`. The registry dispatches on
   `(routine_type, language)`.

2. **SQL scalar UDFs** → `CREATE OR REPLACE MACRO schema.name(args)
   AS (translated_body)`. The body goes through the existing
   SQLTranslator so BigQuery built-ins (SAFE_DIVIDE, STRING functions,
   etc.) transpile correctly. `OR REPLACE` makes update idempotent.

3. **Table-valued functions** → `CREATE OR REPLACE MACRO schema.name(args)
   AS TABLE (translated_body)`. DuckDB's table macros support
   parameter substitution the same way scalar macros do.

4. **JavaScript UDFs** → `DuckDBPyConnection.create_function(qualified_name,
   py_callable, parameter_types, return_type)`. The callable is a thin
   Python wrapper that:
 - Invokes `mini-racer.MiniRacer.call(name, *args)` with a per-routine
   V8 context shared across calls (cheap to reuse, expensive to
   construct).
 - Enforces a configurable CPU timeout (default 5 s) and memory cap
   (default 256 MiB) per invocation via `set_hard_memory_limit` and
   the `timeout_sec=` eval kwarg.
 - Converts Arrow/Python values to JSON on the way in and JSON on the
   way out, matching BigQuery's UDF type coercion.

5. **Hydration on startup.** After `DuckDBCatalogRepository.ensure_ready()`
   runs, the server walks every routine and re-materializes it. This
   keeps behaviour consistent across ephemeral and persistent modes.

6. **Schema provisioning.** Every routine's `project_id__dataset_id`
   DuckDB schema must exist before `CREATE MACRO` can succeed; the
   registry calls `CREATE SCHEMA IF NOT EXISTS` through the validated
   identifier helpers.

7. **Sandboxing for JS UDFs.** `mini-racer` provides no network, no
   filesystem, and no `require()`. The V8 heap cap + per-call CPU
   timeout protects against run-away JS. Any `JSTimeoutException` /
   `JSOOMException` is surfaced as an `InvalidQueryError` with a clear
   message.

## Consequences

- **Positive:** CREATE FUNCTION errors fire at CREATE time, not query
  time. Match BigQuery's behaviour and let clients fix mistakes before
  a workload hits them.
- **Positive:** No coupling between the translator and the catalog —
  the translator still operates purely on AST, and the UDF lives as a
  DuckDB-native callable.
- **Positive:** Single registration point means every invocation site
  (ordinary query, CALL from a script, inline inside another UDF) sees
  the same registered function.
- **Positive:** Deterministic hydration on startup — a persistent-mode
  emulator restart restores UDFs from the catalog before serving
  traffic.
- **Negative:** `CREATE MACRO OR REPLACE` + `create_function` both hold
  the DuckDB write lock; on a very large routine churn, CREATE FUNCTION
  requests serialise behind write traffic. Acceptable: the emulator is
  single-process, and UDFs are typically created at setup, not steady
  state.
- **Negative:** Deleting a SQL UDF requires `DROP MACRO IF EXISTS`;
  JS UDFs require `DuckDBPyConnection.remove_function`. Deregistration
  is idempotent to match create's `OR REPLACE` semantics.
