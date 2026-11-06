# ADR 0049: Run query statements off the event loop

- **Status**: Proposed

## Context

[ADR 0005](0005-fastapi-grpcaio-single-process.md) runs REST and gRPC on one
asyncio event loop and expects DuckDB to keep it responsive because DuckDB
releases the GIL while it executes. That only helps other *threads*.
`DuckDBEngine.execute` and `fetch_arrow` are synchronous and are called from
coroutines, so the loop thread itself waits for every statement. While a long
query runs, the emulator serves nothing else: not other jobs, not job polling,
not `/healthz`.

Measured on `main` with a single query that takes about 3.9 s
(`COUNT(DISTINCT …)` over a 12000 × 12000 `UNNEST` cross join), polling
`/healthz` every 50 ms from another client:

| | query | `/healthz` answered during the query | slowest `/healthz` |
| --- | --- | --- | --- |
| `main` | 3.93 s | 1 | 3.72 s |
| this ADR | 3.89 s | 56 | 0.02 s |

In practice this shows up as clients timing out behind an unrelated query,
and as container orchestrators restarting the emulator: a Kubernetes liveness
probe on `/healthz` (default 1 s timeout) killed it repeatedly while an
Estuary CDC backfill was running MERGE statements against it.

## Decision

Single-statement query jobs (`jobs.query`, `jobs.insert` with a query
configuration) execute through `DuckDBEngine.fetch_arrow_off_loop`, which
runs the statement in a worker thread and awaits the result.

- **A separate cursor, not the shared connection.** The worker uses
  `connection.cursor()`, a second connection to the same DuckDB database.
  `DuckDBEngine.execute` returns the connection itself and callers fetch from
  it afterwards, so a statement from another thread on the same connection
  could replace a result between a caller's `execute` and its `fetch*`.
  Giving the worker its own cursor keeps the main connection single-threaded.
- **One worker thread.** Query statements still run one at a time, in
  submission order, as they did on the loop. DuckDB already parallelises a
  single statement across cores; the change is about not blocking the loop,
  not about running more statements at once.
- **`SET GLOBAL TimeZone = 'UTC'`.** A cursor does not inherit session
  settings, so the UTC setting moves from session to global scope. Everything
  else the query path relies on is database-wide and visible to the cursor:
  tables, views, schemas, SQL UDF and TVF macros (also those created after
  the cursor), Python UDFs registered with `create_function` (builtin and JS
  UDFs, also after the cursor) and loaded extensions.

Scripts, load and extract jobs, `tabledata.insertAll`, and the Storage Read
and Write APIs keep running on the main connection, on the loop.

## Consequences

- **Positive**: the loop keeps serving requests, health checks included,
  while a query statement runs. Query latency is unchanged.
- **Negative**: connection-local objects on the main connection (`TEMP`
  tables and views, `connection.register` views) are not visible to query
  statements, and vice versa. No code path creates them on one side and reads
  them from the other today; a single-statement `CREATE TEMP TABLE` in a
  session lives on the query cursor, where the session's later query jobs also
  run.
- **Negative**: a query statement can now run at the same time as work on
  the main connection (a load job, `insertAll`, a script, catalog updates).
  DuckDB's MVCC isolates them; two transactions that update the same rows
  fail with a transaction conflict instead of being serialised.
- **Follow-up**: scripts, load jobs and Storage Read sessions can block the
  loop the same way. Moving them needs care with connection-local state
  (scripts use `TEMP` objects across statements; loads use
  `connection.register`), so they are left for separate changes.
