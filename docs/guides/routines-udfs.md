# Routines and UDFs

bqemulator supports four routine kinds, matching BigQuery's public
surface. All four are first-class — created, invoked, and introspected
exactly as you would against the real service.

| Kind | Language | Runtime | Examples |
|---|---|---|---|
| SQL scalar UDF | SQL | DuckDB `CREATE MACRO` | `add_one(x)`, `format_money(cents)` |
| Table-valued function | SQL | DuckDB `CREATE MACRO... AS TABLE` | `recent_events(cutoff)` |
| JavaScript scalar UDF | JavaScript | Embedded V8 via [`mini-racer`](../adr/0010-py-mini-racer-for-js-udfs.md) | Regex, JSON, custom math |
| Stored procedure | SQL procedural | Scripting interpreter ([ADR 0015](../adr/0015-scripting-execution-model.md)) | `create_orders(qty)` |

Under the hood, the `UDFRegistry` (see
[ADR 0014](../adr/0014-udf-materialization-strategy.md)) eagerly
registers each routine with DuckDB on REST CRUD and re-hydrates on
startup, so CREATE-time syntax errors fire immediately and invocations
never pay registration cost.

## Quick start — SQL scalar UDF

```python
from google.cloud import bigquery

client = bigquery.Client(project="my-project", ...)
ref = bigquery.RoutineReference.from_string("my-project.my_ds.add_one")
routine = bigquery.Routine(ref)
routine.type_ = "SCALAR_FUNCTION"
routine.language = "SQL"
routine.arguments = [
    bigquery.RoutineArgument(
        name="x",
        data_type=bigquery.StandardSqlDataType(
            type_kind=bigquery.StandardSqlTypeNames.INT64,
        ),
    ),
]
routine.return_type = bigquery.StandardSqlDataType(
    type_kind=bigquery.StandardSqlTypeNames.INT64,
)
routine.body = "x + 1"
client.create_routine(routine)

job = client.query("SELECT my_ds.add_one(41) AS answer")
print(next(job.result())["answer"])  # -> 42
```

## Table-valued function

A TVF returns a table; its body is a full ``SELECT`` and it is
referenced in a ``FROM`` clause.

```python
routine.type_ = "TABLE_VALUED_FUNCTION"
routine.language = "SQL"
routine.arguments = [
    bigquery.RoutineArgument(
        name="n",
        data_type=bigquery.StandardSqlDataType(
            type_kind=bigquery.StandardSqlTypeNames.INT64,
        ),
    ),
]
routine.body = "SELECT i AS value FROM UNNEST(GENERATE_ARRAY(1, n)) AS i"
client.create_routine(routine)

job = client.query("SELECT SUM(value) AS total FROM my_ds.one_to_n(10)")
print(next(job.result())["total"])  # -> 55
```

## JavaScript scalar UDF

JavaScript UDFs run in an isolated V8 context per routine. Arguments
arrive as standard JSON types; return values round-trip through JSON.

```python
routine.type_ = "SCALAR_FUNCTION"
routine.language = "JAVASCRIPT"
routine.arguments = [
    bigquery.RoutineArgument(
        name="x",
        data_type=bigquery.StandardSqlDataType(
            type_kind=bigquery.StandardSqlTypeNames.INT64,
        ),
    ),
]
routine.return_type = bigquery.StandardSqlDataType(
    type_kind=bigquery.StandardSqlTypeNames.INT64,
)
routine.body = "return x * 2;"
client.create_routine(routine)
```

### Sandboxing

- **No network.** ``mini-racer`` has no ``fetch`` / ``XMLHttpRequest``.
- **No filesystem.** No ``require``, ``import``, or ``readFileSync``.
- **Per-routine memory cap** (``BQEMU_UDF_JS_MEMORY_BYTES`` — default
  256 MiB). Exceeding it raises ``InvalidQueryError`` with a clear
  message.
- **Per-invocation CPU timeout** (``BQEMU_UDF_JS_TIMEOUT_MS`` — default
  5 000 ms). Best-effort: under some asyncio contexts the timeout
  falls back to the memory cap only. We document this trade-off in
  [ADR 0014](../adr/0014-udf-materialization-strategy.md).

## Stored procedures

Procedures are named scripts. They may use every scripting construct
(see [scripting.md](scripting.md)) and are invoked with ``CALL``.

```sql
CREATE OR REPLACE PROCEDURE my_ds.square(x INT64)
BEGIN
  SELECT x * x;
END;

CALL my_ds.square(7);  -- -> 49
```

Procedures open a fresh lexical scope on entry; they do not see the
caller's local variables. Arguments are the only bindings in the
procedure's initial frame.

## INFORMATION_SCHEMA.ROUTINES

The emulator exposes the same catalog view BigQuery publishes:

```sql
SELECT routine_name, routine_type, language, ddl
FROM my_ds.INFORMATION_SCHEMA.ROUTINES
ORDER BY routine_name;
```

Columns:

| Column | Description |
|---|---|
| ``specific_catalog`` / ``routine_catalog`` | project id |
| ``specific_schema`` / ``routine_schema`` | dataset id |
| ``specific_name`` / ``routine_name`` | routine id |
| ``routine_type`` | ``SCALAR_FUNCTION``, ``TABLE_VALUED_FUNCTION``, ``PROCEDURE`` |
| ``language`` | ``SQL`` or ``JAVASCRIPT`` |
| ``routine_body`` | ``SQL`` or ``EXTERNAL`` |
| ``data_type`` | return type kind (scalar UDFs only) |
| ``created`` / ``last_altered`` | routine timestamps |
| ``ddl`` | synthesised ``CREATE FUNCTION``/``PROCEDURE`` DDL |

## Configuration

| Environment variable | Default | Notes |
|---|---|---|
| ``BQEMU_UDF_JS_TIMEOUT_MS`` | 5 000 | Per-invocation CPU cap. |
| ``BQEMU_UDF_JS_MEMORY_BYTES`` | 268 435 456 (256 MiB) | V8 heap cap. |
| ``BQEMU_SCRIPTING_MAX_STATEMENTS`` | 10 000 | Per-script statement cap. |
| ``BQEMU_SCRIPTING_MAX_LOOP_ITERATIONS`` | 1 000 000 | Per-loop iteration cap. |

## Known limitations

- Named ``@`` query parameters cannot be used from *inside* a script
  body; reference script variables by their declared name instead.
- ``EXCEPTION WHEN`` only supports ``WHEN ERROR THEN`` — specific
  condition names are out of scope for v1 (see
  [out-of-scope.md](../reference/out-of-scope.md)).
- JavaScript UDFs may block a worker thread beyond ``cpu_timeout_ms``
  in async contexts; the hard memory cap still applies and prevents
  unbounded allocation. This is documented explicitly in
  [ADR 0014](../adr/0014-udf-materialization-strategy.md).
