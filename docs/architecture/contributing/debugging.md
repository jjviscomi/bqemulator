# Debugging

## Reading logs

Run with pretty console output:

```bash
bqemulator start --ephemeral --log-level debug --log-format console
```

Every line carries a `correlation_id`. Grep by correlation id to follow
a request:

```bash
bqemulator ... 2>&1 | grep '"correlation_id": "abcd1234"'
```

## Inspecting DuckDB state

With the admin interface enabled (`--enable-admin`):

```
curl http://localhost:9050/admin/catalog | jq .
curl http://localhost:9050/admin/jobs | jq .
curl http://localhost:9050/admin/streams | jq .
```

To attach DuckDB's own CLI to a persistent instance:

```bash
duckdb ~/.bqemulator/bqemulator.duckdb
> SHOW ALL TABLES;
> SELECT * FROM "_bqemulator_catalog"."datasets";
```

!!! warning
    Attaching a second writer process to the DuckDB file will conflict
    with the running emulator. Stop the emulator first, or attach with
    `duckdb -readonly`.

## Reproducing a failing query

Reduce to a minimal script:

```python
from bqemulator.sql.translator import SQLTranslator
t = SQLTranslator()
print(t.translate("<your failing BigQuery SQL>"))
```

If translation succeeds but execution fails, reproduce against DuckDB
directly:

```python
import duckdb
conn = duckdb.connect()
conn.execute("<translated DuckDB SQL>")
```

## Capturing traces

Set `BQEMU_TRACING_ENABLED=true` and `BQEMU_OTLP_ENDPOINT=localhost:4317`.
Run Jaeger or Tempo locally and look for the `bqemulator` service.
