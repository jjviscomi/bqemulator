# UDFs

Implementation in `src/bqemulator/udf/`.

## Three runtimes

| Type | Runtime | Registration |
|---|---|---|
| SQL UDF (scalar) | DuckDB `CREATE MACRO` | `sql_udf.py` |
| Table-valued function | DuckDB macro returning TABLE | `table_valued.py` |
| JavaScript UDF | Embedded V8 via `mini-racer` | `js_udf.py` |

## Invocation

`runtime.py` is the dispatcher. When the SQL translator encounters a
UDF reference, it calls into the runtime to either:

- Translate the UDF body into the output SQL (SQL UDFs — inlined into
  the query).
- Register a UDF function handler that DuckDB can call back into
  (JavaScript UDFs — each call shuttles through Python ↔ V8).

## JavaScript UDFs

Argument conversion follows BigQuery's documented semantics:

| BigQuery type | JS type |
|---|---|
| INT64 | Number (with precision caveat for values outside Number range) |
| FLOAT64 | Number |
| STRING, BYTES | String |
| BOOL | Boolean |
| TIMESTAMP, DATE, DATETIME | String (ISO 8601) |
| ARRAY<T> | Array |
| STRUCT<…> | Object |
| JSON | Object / Array / primitive |

Each V8 context is reused across calls within a query but destroyed at
query completion. Resource limits:

- CPU time: 5 seconds per invocation (configurable)
- Memory: 256 MiB per context (configurable)

No network, no filesystem — `mini-racer` exposes neither.
