# SQL function mapping

The rule registry in
[`src/bqemulator/sql/rules/`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/)
ships BigQuery → DuckDB translation rules that run after the SQLGlot
transpile step; the rewriter pipeline in
[`src/bqemulator/sql/rewriter/`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/)
runs before it. Both surfaces are enumerated in the auto-generated
**Rule + rewriter registry** table further down — that table is
regenerated from
[`scripts/generate_function_mapping.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/generate_function_mapping.py)
on every `make verify` (and on every PR via the per-PR
`docs-drift-check` job in
[`ci.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/ci.yml)),
so the rule count and per-row cell text stay in lock-step with the
live source. The INFORMATION_SCHEMA per-view rewriter table below
is hand-maintained — that family has a 1:1 mapping to BigQuery's
catalog views that's clearer in narrative form.

See [architecture/sql-translation.md](../architecture/sql-translation.md) for
how rules are registered, and
[contributing/adding-sql-functions.md](../architecture/contributing/adding-sql-functions.md)
for a walkthrough of adding a new rule.

## INFORMATION_SCHEMA rewriter mapping

The INFORMATION_SCHEMA family of virtual catalog views is
implemented by the pre-translation rewriter at
[`src/bqemulator/sql/rewriter/information_schema.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/information_schema.py).
Each view is replaced inline with a `VALUES` subquery sourced
from the catalog before the BQ → DuckDB transpile step. The
table below maps each BigQuery view to its rewriter function.

| BigQuery view | Rewriter function | Source data |
|---|---|---|
| `INFORMATION_SCHEMA.SCHEMATA` | `expand_information_schema_schemata` | `catalog.list_datasets()` |
| `INFORMATION_SCHEMA.TABLES` | `expand_information_schema_tables` | `catalog.list_tables()` |
| `INFORMATION_SCHEMA.COLUMNS` | `expand_information_schema_columns` | per-table `TableSchema.fields` |
| `INFORMATION_SCHEMA.TABLE_OPTIONS` | `expand_information_schema_table_options` | `TableMeta.description / labels / friendly_name / expiration_time / time_partitioning` |
| `INFORMATION_SCHEMA.VIEWS` | `expand_information_schema_views` | tables filtered to `table_type='VIEW'` |
| `INFORMATION_SCHEMA.PARTITIONS` | `expand_information_schema_partitions` | live DuckDB GROUP-BY on the partition column |
| `INFORMATION_SCHEMA.ROUTINES` | `expand_information_schema_routines` | `catalog.list_routines()` |
| `INFORMATION_SCHEMA.MATERIALIZED_VIEWS` | `expand_information_schema_materialized_views` | `catalog.list_materialized_views()` |
| `INFORMATION_SCHEMA.ROW_ACCESS_POLICIES` | `expand_information_schema_row_access_policies` | `catalog.list_all_row_access_policies()` |
| `INFORMATION_SCHEMA.JOBS` / `JOBS_BY_*` | — | [Out of scope](out-of-scope.md#information_schemajobs-family) |

<!-- BEGIN AUTO-GENERATED RULE REGISTRY -->

## Rule + rewriter registry

> **Auto-generated.** Edit translation rules under [`src/bqemulator/sql/rules/`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/) or rewriters under [`src/bqemulator/sql/rewriter/`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/), then run `make function-mapping` to regenerate this block. The CI gate (`--check`) refuses to merge a PR whose committed registry has drifted from the live source. Per-rule docstring summaries are extracted as the cell text — if a cell reads wrong, edit the rule's docstring.

- **Registered rules**: 92 (13 rule modules)
- **Rewriter functions**: 24 (24 rewriter modules; the INFORMATION_SCHEMA rewriter has its own hand-maintained per-view table below)

### Translation rules (post-transpile AST passes)

| Category | BigQuery surface | DuckDB equivalent | Rule / function |
|---|---|---|---|
| Aggregate / window | `ARRAY_CONCAT_AGG(arr [ORDER BY …])` | `flatten(array_agg(arr [ORDER BY …]))` | `ARRAY_CONCAT_AGG` |
| Aggregate / window | `AVG(decimal_col)` | `CAST(AVG(decimal_col) AS DECIMAL(38, 9))` | `AVG_DECIMAL` |
| Aggregate / window | `<dec> / <x>` or `<x> / <dec>` | `CAST(... AS DECIMAL(38, 9))` | `DIV_DECIMAL` |
| Aggregate / window | `HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x [, precision]))` | `COUNT(DISTINCT x)` | `HLL_COUNT_EXTRACT_INIT` |
| Aggregate / window | `HLL_COUNT.MERGE(col)` | `COUNT(DISTINCT col)` over an inlined source subquery | `HLL_COUNT_MERGE` |
| Array helpers | `ARRAY_FIRST(arr)` | empty-check CASE around `list_extract(arr, 1)` | `ARRAY_FIRST` |
| Array helpers | `ARRAY_LAST(arr)` | empty-check CASE around `list_extract(arr, -1)` | `ARRAY_LAST` |
| Date / time / timestamp | `APPROX_COUNT_DISTINCT(x)` | `COUNT(DISTINCT x)` | `APPROX_COUNT_DISTINCT_EXACT` |
| Date / time / timestamp | `APPROX_QUANTILE(x, [q...])` | `QUANTILE_DISC(x, [q...])` | `APPROX_QUANTILES_DISCRETE` |
| Date / time / timestamp | `ts AT TIME ZONE '+HH:MM' / '-HH:MM'` | interval arithmetic | `AT_TIME_ZONE_NUMERIC_OFFSET` |
| Date / time / timestamp | `a \|\| b` | `CAST(a \|\| b AS VARCHAR)` | `CONCAT_STRING_TYPE` |
| Date / time / timestamp | `EXTRACT(DATE FROM ts)` | `CAST(ts AS DATE)` | `EXTRACT_DATE_FROM_TS` |
| Date / time / timestamp | `EXTRACT(DAYOFWEEK FROM x)` | `EXTRACT(DAYOFWEEK FROM x) + 1` | `EXTRACT_DAYOFWEEK` |
| Date / time / timestamp | `EXTRACT(WEEK FROM x)` | Sunday-start week number | `EXTRACT_WEEK_SUNDAY_START` |
| Date / time / timestamp | `FORMAT(fmt, args…)` | `printf(fmt, args…)` | `FORMAT_PRINTF` |
| Date / time / timestamp | `FORMAT_TIME(fmt, t)` | `STRFTIME(DATE '1970-01-01' + t, fmt)` | `FORMAT_TIME` |
| Date / time / timestamp | `JSON_TYPE(x)` | `LOWER(JSON_TYPE(x))` | `JSON_TYPE_LOWER` |
| Date / time / timestamp | `PARSE_DATETIME(fmt, value)` | `strptime(value, fmt)` | `PARSE_DATETIME` |
| Date / time / timestamp | `PARSE_TIME(value, fmt)` | `CAST(strptime(value, fmt) AS TIME)` | `PARSE_TIME` |
| Date / time / timestamp | `STRPTIME(value, fmt)` | `timezone('UTC', STRPTIME(value, fmt))` | `PARSE_TIMESTAMP_UTC` |
| Date / time / timestamp | `DATE_TRUNC('WEEK', ts AT TIME ZONE 'X')` | Sunday-start truncation | `TIMESTAMP_TRUNC_WEEK_ZONE_SUNDAY` |
| Date / time / timestamp | `TIME(timestamp)` | `CAST(timezone('UTC', timestamp) AS TIME)` | `TIME_FROM_TIMESTAMPTZ` |
| Date / time / timestamp | `TIME_TRUNC(t, unit)` | `CAST(DATE_TRUNC(unit, DATE '1970-01-01' + t) AS TIME)` | `TIME_TRUNC` |
| Interval helpers | `JUSTIFY_DAYS(x)` | DuckDB normalisation expression | `JUSTIFY_DAYS` |
| Interval helpers | `JUSTIFY_HOURS(x)` | DuckDB normalisation expression | `JUSTIFY_HOURS` |
| Interval helpers | `JUSTIFY_INTERVAL(x)` | both JUSTIFY_HOURS + JUSTIFY_DAYS rules combined | `JUSTIFY_INTERVAL` |
| ISO date parts | `DATE_TRUNC(date, DAY\|MONTH\|QUARTER\|YEAR)` | `CAST(... AS DATE)` | `DATE_TRUNC_CALENDAR_UNIT` |
| ISO date parts | `DATE_TRUNC(date, ISOYEAR)` | `CAST(DATE_TRUNC('ISOYEAR', date) AS DATE)` | `DATE_TRUNC_ISOYEAR` |
| ISO date parts | `DATE_TRUNC(date, WEEK)` | Sunday-start truncation cast to DATE | `DATE_TRUNC_WEEK` |
| ISO date parts | `EXTRACT(ISOWEEK FROM x)` | `EXTRACT(WEEK FROM x)` | `EXTRACT_ISOWEEK` |
| JSON helpers | `JSON_ARRAY_INSERT(j, path, value)` | helper-UDF call wrapped in `CAST(... AS JSON)` | `JSON_ARRAY_INSERT` |
| JSON helpers | `JSON_KEYS(json)` | `json_keys(json)` | `JSON_KEYS` |
| JSON helpers | `JSON_QUERY(j, path)` | `CAST(j -> path AS VARCHAR)` | `JSON_QUERY` |
| JSON helpers | `JSON_REMOVE(json, path)` | `bqemu_json_remove(json, path)` | `JSON_REMOVE` |
| JSON helpers | `JSON_SET(json, path, value)` | `bqemu_json_set(j, p, to_json(v))` | `JSON_SET` |
| JSON helpers | `JSON_STRIP_NULLS(json)` | `bqemu_json_strip_nulls(json)` | `JSON_STRIP_NULLS` |
| JSON helpers | `BOOL(json)` | `CAST(json AS BOOLEAN)` | `JSON_VALUE_BOOL` |
| JSON helpers | `FLOAT64(json)` | `CAST(json AS DOUBLE)` | `JSON_VALUE_FLOAT64` |
| JSON helpers | `STRING(json)` | `json_extract_string(json, '$')` | `JSON_VALUE_STRING` |
| JSON helpers | `LAX_BOOL(json)` | `TRY_CAST(json_extract_string(j, '$') AS BOOLEAN)` | `LAX_BOOL` |
| JSON helpers | `LAX_FLOAT64(json)` | `TRY_CAST(json_extract_string(j, '$') AS DOUBLE)` | `LAX_FLOAT64` |
| JSON helpers | `LAX_INT64(json)` | `TRY_CAST(json_extract_string(j, '$') AS BIGINT)` | `LAX_INT64` |
| JSON helpers | `LAX_STRING(json)` | `json_extract_string(json, '$')` | `LAX_STRING` |
| Math / numeric / misc | `APPROX_TOP_SUM(value, weight, k)` | `approx_top_k(value, k)` | `APPROX_TOP_SUM` |
| Math / numeric / misc | `COUNTIF(p)` | `COALESCE(COUNTIF(p), 0)` (wrapped only when needed) | `COUNTIF_EMPTY_ZERO` |
| Math / numeric / misc | `FARM_FINGERPRINT(s)` | `bqemu_farm_fingerprint(s)` | `FARM_FINGERPRINT` |
| Math / numeric / misc | `IEEE_DIVIDE(a, b)` | `CAST(a AS DOUBLE) / CAST(b AS DOUBLE)` | `IEEE_DIVIDE` |
| Math / numeric / misc | `RANGE_BUCKET(point, boundaries)` | `len(list_filter(boundaries, x -> x <= point))` | `RANGE_BUCKET` |
| Math / numeric / misc | `SIGN(<float_arg>)` | NaN-aware FLOAT64 wrapper | `SIGN_FLOAT_TYPE` |
| Numeric type helpers | `PARSE_BIGNUMERIC(s)` | `bqemu_to_bignumeric(s)` | `PARSE_BIGNUMERIC` |
| Numeric type helpers | `PARSE_NUMERIC(s)` | `CAST(s AS DECIMAL(38, 9))` | `PARSE_NUMERIC` |
| RANGE<T> constructors | `GENERATE_RANGE_ARRAY(r, step)` | list of consecutive sub-ranges | `GENERATE_RANGE_ARRAY` |
| RANGE<T> constructors | `RANGE(start, end)` | STRUCT_PACK constructor | `RANGE` |
| RANGE<T> constructors | `RANGE_CONTAINS(r, value)` | `(r."start" <= value AND value < r."end")` | `RANGE_CONTAINS` |
| RANGE<T> constructors | `RANGE_END(r)` | `r."end"` | `RANGE_END` |
| RANGE<T> constructors | `RANGE_INTERSECT(r1, r2)` | `CASE WHEN overlaps THEN STRUCT_PACK(...) END` | `RANGE_INTERSECT` |
| RANGE<T> constructors | `RANGE_OVERLAPS(r1, r2)` | `r1."start" < r2."end" AND r2."start" < r1."end"` | `RANGE_OVERLAPS` |
| RANGE<T> constructors | `RANGE_START(r)` | `r."start"` | `RANGE_START` |
| Reciprocal trig | `COTH(x)` | `1.0 / TANH(x)` (DuckDB has no native `COTH`) | `COTH` |
| Reciprocal trig | `CSC(x)` | `1.0 / SIN(x)` (DuckDB has no native `CSC`) | `CSC` |
| Reciprocal trig | `CSCH(x)` | `1.0 / SINH(x)` (DuckDB has no native `CSCH`) | `CSCH` |
| Reciprocal trig | `SEC(x)` | `1.0 / COS(x)` (DuckDB has no native `SEC`) | `SEC` |
| Reciprocal trig | `SECH(x)` | `1.0 / COSH(x)` (DuckDB has no native `SECH`) | `SECH` |
| SAFE.X arithmetic | `SAFE_ADD(a, b)` | `TRY(a + b)` | `SAFE_ADD` |
| SAFE.X arithmetic | `SAFE_MULTIPLY(a, b)` | `TRY(a * b)` | `SAFE_MULTIPLY` |
| SAFE.X arithmetic | `SAFE_NEGATE(a)` | `TRY(0 - a)` | `SAFE_NEGATE` |
| SAFE.X arithmetic | `SAFE_SUBTRACT(a, b)` | `TRY(a - b)` | `SAFE_SUBTRACT` |
| GEOGRAPHY (spheroidal) | `GEOGRAPHY` column type | `GEOMETRY` | `GEOGRAPHY_COLUMN_TYPE` |
| GEOGRAPHY (spheroidal) | Rename BigQuery `ST_*` calls to their DuckDB equivalents | — | `ST_*_RENAME` |
| GEOGRAPHY (spheroidal) | `ST_AREA(g)` | `bqemu_st_area_spheroidal(ST_AsText(g))` | `ST_AREA_SPHEROIDAL` |
| GEOGRAPHY (spheroidal) | `ST_AsGeoJSON(g)` | `CAST(ST_AsGeoJSON(g) AS VARCHAR)` | `ST_ASGEOJSON_STRING_TYPE` |
| GEOGRAPHY (spheroidal) | `ST_DISTANCE(g1, g2)` | `bqemu_st_distance_spheroidal(ST_AsText(g1), ST_AsText(g2))` | `ST_DISTANCE_SPHEROIDAL` |
| GEOGRAPHY (spheroidal) | `ST_DWITHIN(g1, g2, d)` | `bqemu_st_distance_spheroidal(g1, g2) <= d` | `ST_DWITHIN_SPHEROIDAL` |
| GEOGRAPHY (spheroidal) | `ST_GEOGFROMWKB(bytes)` | `ST_GeomFromHEXWKB(hex(bytes))` | `ST_GEOGFROMWKB` |
| GEOGRAPHY (spheroidal) | `ST_GeometryType(g)` | CASE mapping DuckDB names → BigQuery names | `ST_GEOMETRYTYPE_BQ_NAME` |
| GEOGRAPHY (spheroidal) | Rewrite `ST_INTERSECTSBOX(g, lo_lng, lo_lat, hi_lng, hi_lat)` | — | `ST_INTERSECTSBOX` |
| GEOGRAPHY (spheroidal) | `ST_ISCOLLECTION(g)` | `ST_GeometryType(g) IN (…)` | `ST_ISCOLLECTION` |
| GEOGRAPHY (spheroidal) | `ST_LENGTH(g)` | `bqemu_st_length_spheroidal(ST_AsText(g))` | `ST_LENGTH_SPHEROIDAL` |
| GEOGRAPHY (spheroidal) | `ST_MAKEPOLYGONORIENTED(ARRAY<GEOGRAPHY>)` | `ST_MakePolygon(arr[1])` | `ST_MAKEPOLYGONORIENTED` |
| GEOGRAPHY (spheroidal) | `ST_MAXDISTANCE(g1, g2)` | `bqemu_st_distance_spheroidal(...)` | `ST_MAXDISTANCE` |
| GEOGRAPHY (spheroidal) | `ST_PERIMETER(g)` | `bqemu_st_perimeter_spheroidal(ST_AsText(g))` | `ST_PERIMETER_SPHEROIDAL` |
| GEOGRAPHY (spheroidal) | `ST_SNAPTOGRID(g, size)` | `ST_GeomFromText(bqemu_st_snaptogrid(ST_AsText(g), size))` | `ST_SNAPTOGRID` |
| String / bytes helpers | `CODE_POINTS_TO_BYTES(arr)` | `bqemu_code_points_to_bytes(arr)` | `CODE_POINTS_TO_BYTES` |
| String / bytes helpers | `CODE_POINTS_TO_STRING(arr)` | `array_to_string(list_transform(arr, x -> chr(x)), '')` | `CODE_POINTS_TO_STRING` |
| String / bytes helpers | `FROM_BASE32(string)` | `bqemu_from_base32(string)` | `FROM_BASE32` |
| String / bytes helpers | `OCTET_LENGTH(x)` (from BigQuery's `BYTE_LENGTH` or `OCTET_LENGTH`) | — | `OCTET_LENGTH` |
| String / bytes helpers | `REGEXP_EXTRACT(...)` | `NULLIF(REGEXP_EXTRACT(...), '')` | `REGEXP_EXTRACT_NULLIF_EMPTY` |
| String / bytes helpers | `SAFE_CONVERT_BYTES_TO_STRING(blob)` | `try(decode(blob))` | `SAFE_CONVERT_BYTES_TO_STRING` |
| String / bytes helpers | `SOUNDEX(s)` | `bqemu_soundex(s)` | `SOUNDEX` |
| String / bytes helpers | `TO_BASE32(blob)` | `bqemu_to_base32(blob)` | `TO_BASE32` |
| String / bytes helpers | `TO_CODE_POINTS(s)` | `list_transform(string_split(s, ''), c -> ord(c))` | `TO_CODE_POINTS` |
| String / bytes helpers | `UPPER(s)` | `bqemu_upper_unicode(s)` | `UPPER_UNICODE` |

### Pre-translator rewriters (run before SQLGlot transpile)

| Category | BigQuery surface | DuckDB equivalent | Rule / function |
|---|---|---|---|
| Pre-translator (aggregate variants) | Pre-translate BigQuery aggregate variants DuckDB rejects | — | `rewrite_aggregate_variants` |
| Pre-translator (COLLATE) | Pre-translate BigQuery SQL for the `COLLATE(value, specifier)` form | — | `rewrite_collate_specifier` |
| Pre-translator (CTAS schema) | Convert combined `CREATE TABLE x (schema) AS SELECT …` to bare CTAS | — | `rewrite_create_table_schema_ctas` |
| Pre-translator (date / time) | Pre-translate BigQuery SQL for date/time functions with lossy transpiles | — | `rewrite_datetime_helpers` |
| Pre-translator (decimal literals) | Pin bare BigQuery decimal literals to `FLOAT64`-typed form | — | `rewrite_decimal_literals` |
| Pre-translator (default dataset) | Rewrite unqualified table refs in `bq_sql` to the default project + dataset | — | `qualify_unqualified_tables` |
| Pre-translator (division by zero) | Pre-translate BigQuery SQL to raise on `a / 0` for the bare `/` operator | — | `rewrite_division_by_zero` |
| Pre-translator (JSON helpers) | Pre-translate BigQuery SQL for JSON functions with lossy transpiles | — | `rewrite_json_helpers` |
| Pre-translator (legacy SQL) | Return `bq_sql` with the legacy-SQL subset rewritten to standard SQL | — | `rewrite_legacy_to_standard` |
| Pre-translator (numeric literals) | Pre-translate BigQuery NUMERIC / BIGNUMERIC typed literals | — | `rewrite_numeric_literals` |
| Pre-translator (partition pseudo-columns) | Return `bq_sql` with the partition pseudo-columns substituted | — | `rewrite_partition_pseudo_columns` |
| Pre-translator (RANGE_SESSIONIZE) | Replace every `RANGE_SESSIONIZE(...)` call with a windowed subquery | — | `rewrite_range_sessionize` |
| Pre-translator (row-access) | Apply caller-bound row access policies to `bq_sql` | — | `rewrite_for_row_access` |
| Pre-translator (SAFE.X prefix) | Pre-translate BigQuery SQL for the `SAFE.X` prefix form | — | `rewrite_safe_helpers` |
| Pre-translator (SHA-512) | Pre-translate every `SHA512(x)` BigQuery call to `bqemu_sha512(x)` | — | `rewrite_sha512` |
| Pre-translator (RANGE/INTERVAL literals) | Pre-translate BigQuery SQL for specialized-type literal forms | — | `rewrite_specialized_types` |
| Pre-translator (string helpers) | Pre-translate BigQuery NORMALIZE / NORMALIZE_AND_CASEFOLD / 4-arg INSTR | — | `rewrite_string_helpers` |
| Pre-translator (STRUCT helpers) | Pre-translate BigQuery SQL for positional `STRUCT` literals | — | `rewrite_struct_helpers` |
| Pre-translator (TIMESTAMP ISO helpers) | Pre-translate `FORMAT_TIMESTAMP` / `PARSE_TIMESTAMP` to the helper UDFs | — | `rewrite_timestamp_iso_helpers` |
| Pre-translator (UNNEST WITH OFFSET) | Rebase `WITH OFFSET` columns to 0-based semantics | — | `rewrite_unnest_offset` |
| Pre-translator (UNNEST STRUCT aliases) | Propagate named-struct field aliases inside `UNNEST([...])` arrays | — | `rewrite_unnest_struct` |
| Pre-translator (wildcard tables) | Expand every wildcard table reference in `bq_sql` | — | `expand_wildcard_tables` |
| Other rewriter | Return a no-op statement when `bq_sql` is `ALTER TABLE ... SET OPTIONS(...)` | — | `rewrite_alter_table_set_options` |
| Other rewriter | Pre-translate BigQuery SQL for every caller-identity spelling | — | `rewrite_session_user` |

<!-- END AUTO-GENERATED RULE REGISTRY -->
