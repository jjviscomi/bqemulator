# Specialized types architecture

The emulator supports three BigQuery types — `GEOGRAPHY`, `RANGE<T>`, and
`INTERVAL` — none of which map 1:1 with a DuckDB type. Each is backed
by a *modelled* DuckDB representation plus a thin translation layer.

## Module map

| Concern | Module |
|-------------------------------|-----------------------------------------------------|
| Function-name and shape table | `types/geography.py` |
| WKB → WKT helper | `types/geography.py` (`wkb_to_wkt`) |
| RANGE element-type validation | `types/range_type.py` |
| Interval literal parser | `types/interval.py` (`parse_interval_literal`) |
| JUSTIFY expression builder | `types/interval.py` (`justify_*_expr`) |
| BQ canonical interval emit | `types/interval.py` (`format_bq_interval`) |
| Type mapping | `storage/type_map.py` |
| REST schema round-trip | `api/routes/tables.py`, `api/routes/tabledata.py` |
| Arrow ↔ REST wire | `storage/arrow_bridge.py` |
| Pre-translator rewrites | `sql/rewriter/specialized_types.py` |
| Post-translator SQL rules | `sql/rules/{spatial,range_rules,interval_rules}.py` |

## Backing representations

### GEOGRAPHY → DuckDB GEOMETRY

DuckDB's `spatial` extension is **required** at engine startup
(`storage/engine.py::_load_spatial`). The emulator fails fast with a
clear `InternalError` if `INSTALL spatial` or `LOAD spatial` fails —
GEOGRAPHY queries cannot be emulated without it.

Storage on disk: WKB (DuckDB's native GEOMETRY representation).
Wire format on the way out: WKT, produced by a lazy in-process
DuckDB conversion connection in `types/geography.py::wkb_to_wkt`.
The conversion connection avoids a heavyweight shapely / geoarrow
dependency.

Trade-off: DuckDB's `GEOMETRY` is planar (Cartesian) while BigQuery's
`GEOGRAPHY` is spheroidal. Distance, area, and perimeter values
diverge at continental scales. Documented in
[ADR 0019](../adr/0019-specialized-types.md).

### RANGE<T> → STRUCT<start T, "end" T>

DuckDB has no native RANGE. The emulator models it as a two-field
struct. The field names `start` and `end` mirror the BigQuery
projection names — `r.start`, `r.end`. In DDL the field names are
double-quoted because `end` is a SQL keyword.

The constructor `RANGE(a, b)` is rewritten **before** SQLGlot's
transpile pass to a BigQuery STRUCT literal
(`STRUCT(a AS \`start\`, b AS \`end\`)`). This prevents SQLGlot's
DuckDB-side parser from folding the two-argument call into a
`GenerateSeries` node — the same shape it uses for `GENERATE_ARRAY`,
which would lose the distinction between the two.

The `RANGE_*` family (CONTAINS, OVERLAPS, INTERSECT,
GENERATE_RANGE_ARRAY) lives in the post-translator rule pass; each
rule expands the BigQuery call into a DuckDB expression that accesses
the struct's `start` and `end` fields.

`RANGE_SESSIONIZE` is a TVF. A pre-translator at
`src/bqemulator/sql/rewriter/range_sessionize.py` rewrites the
`RANGE_SESSIONIZE(TABLE <ref>, '<range_col>', [<part_cols>] [,
'<sessionize_option>'])` call into a windowed gaps-and-islands
subquery. The rewrite operates on the raw SQL text because
SQLGlot's BigQuery parser rejects the `TABLE <ref>` keyword in TVF
arguments. Mode dispatch matches the documented BigQuery
semantics: `MEETS` (default) and the `OVERLAPS_OR_MEETS` alias use
strict `>` for the new-session predicate so touching ranges share
a session; `OVERLAPS` uses `>=` so touching ranges form separate
sessions. The expansion emits `RANGE(MIN(...) OVER...,
MAX(...) OVER...)` which the existing `_rewrite_range_constructor`
pass picks up and converts to the canonical STRUCT shape; the
resulting `STRUCT("start" T, "end" T)` column lands as `RANGE<T>`
on the REST wire via `detect_range_element`. A second pass
`_rewrite_range_data_types` rewrites `RANGE<T>` column-type
references so DDL like `CREATE TABLE t (col RANGE<DATE>)`
survives the DuckDB parser.

### INTERVAL → DuckDB INTERVAL

DuckDB's `INTERVAL` is a 3-tuple of months / days / microseconds.
Most BigQuery interval forms map directly: `INTERVAL 1 DAY`,
`MAKE_INTERVAL(...)`, and DATE / TIMESTAMP arithmetic all parse
correctly under DuckDB's grammar.

The exception is BigQuery's compound literal
`INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND`, which DuckDB's parser
refuses. The pre-translator rewriter parses the string in Python
(`types/interval.parse_interval_literal`) and emits the equivalent
sum of single-unit intervals.

`JUSTIFY_HOURS` / `JUSTIFY_DAYS` / `JUSTIFY_INTERVAL` are absent from
DuckDB and synthesised from `extract` + `to_<unit>` primitives at
translate time.

## Translation pipeline

```
BigQuery SQL
   │
   ▼
[pre-translator]  sql/rewriter/range_sessionize.py (scope-#15)
   │  - RANGE_SESSIONIZE(TABLE …, …) → windowed gaps-and-islands
   │    subquery emitting RANGE(MIN OVER …, MAX OVER …)
   │    (operates on raw SQL text — SQLGlot rejects TABLE <ref>)
   ▼
[pre-translator]  sql/rewriter/specialized_types.py
   │  - INTERVAL '…-… … …:…:…' YEAR TO SECOND → sum of singles
   │  - RANGE(a, b) (Anonymous in BQ AST) → STRUCT(a AS start, b AS end)
   │  - RANGE<T> '[s, e)' typed literal → STRUCT(CAST AS T)
   │  - RANGE<T> column-type / non-literal-CAST → STRUCT<start T, end T>
   ▼
[SQLGlot transpile]  read=bigquery, write=duckdb
   │
   ▼
[post-translator rule pass]  sql/translator.py::_apply_rules
   │  Walks AST in post-order (reversed pre-order) so children get
   │  rewritten before parents.
   │  Rules:
   │   - ST_*_RENAME  (BQ ST_* → DuckDB ST_* + alias mapping)
   │   - ST_GEOGFROMWKB (wraps arg in hex())
   │   - ST_ISCOLLECTION (→ ST_GeometryType(…) IN (…))
   │   - RANGE_CONTAINS / RANGE_OVERLAPS / RANGE_INTERSECT
   │   - GENERATE_RANGE_ARRAY
   │   - JUSTIFY_HOURS / JUSTIFY_DAYS / JUSTIFY_INTERVAL
   ▼
DuckDB SQL
```

## REST wire format

`tables.insert` / `tables.get` accept and emit:

* `{"type": "GEOGRAPHY"}` — no sub-fields.
* `{"type": "INTERVAL"}` — no sub-fields.
* `{"type": "RANGE", "rangeElementType": {"type": "DATE"}}` — element
  type is `DATE` / `DATETIME` / `TIMESTAMP`. Required when the field
  type is `RANGE`.

`tabledata.insertAll` accepts BigQuery-style JSON values:

* GEOGRAPHY: WKT string (e.g. `"POINT(1 2)"`). The emulator converts
  WKT → hex-WKB in `bq_rows_to_arrow` and emits an
  `INSERT INTO... SELECT ST_GeomFromHEXWKB(col_hex), … FROM <register>`
  for the storage write.
* INTERVAL: BigQuery-canonical string (e.g. `"1-2 3 4:5:6.789"`).
* RANGE: `{"start": "...", "end": "..."}` JSON object.

`getQueryResults` / `tabledata.list` emit:

* GEOGRAPHY column → WKT string in the row.
* INTERVAL column → BigQuery-canonical string (`Y-M D H:M:S[.ffffff]`).
* RANGE column → standard STRUCT nested-row shape.

## See also

* [ADR 0019 — Specialized types](../adr/0019-specialized-types.md)
* [GEOGRAPHY guide](../guides/geography-spatial.md)
* [RANGE guide](../guides/range-types.md)
* [INTERVAL guide](../guides/interval-arithmetic.md)
* [Out of scope](../reference/out-of-scope.md)
