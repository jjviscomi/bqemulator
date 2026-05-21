# Using `INFORMATION_SCHEMA` against the emulator

BigQuery exposes a family of virtual catalog views under
`{project}.{dataset}.INFORMATION_SCHEMA.*` that describe the
catalog itself: datasets, tables, columns, options, views,
partitions, routines, materialized views, and row-access policies.
The emulator implements **9 of the 10 published views** — every
view BigQuery ships except the `JOBS` family. The reference
emulator (`goccy/bigquery-emulator`) covers a subset (4 views);
bqemulator is a superset.

## Supported views

| View | Source of truth |
|---|---|
| `SCHEMATA` | catalog `list_datasets()` |
| `TABLES` | catalog `list_tables()` |
| `COLUMNS` | per-table `TableSchema.fields` |
| `TABLE_OPTIONS` | `TableMeta.description / friendly_name / labels / expiration_time / time_partitioning.require_partition_filter` |
| `VIEWS` | tables filtered to `table_type='VIEW'` |
| `PARTITIONS` | live DuckDB GROUP-BY on the partitioning column |
| `ROUTINES` | catalog `list_routines()` |
| `MATERIALIZED_VIEWS` | catalog `list_materialized_views()` |
| `ROW_ACCESS_POLICIES` | catalog `list_all_row_access_policies()` |

## Scope-qualified references

All three BigQuery scope forms work:

```sql
-- Three-part: project.dataset.INFORMATION_SCHEMA.X
SELECT * FROM `my-project.my_dataset.INFORMATION_SCHEMA.TABLES`;

-- Two-part: dataset.INFORMATION_SCHEMA.X (current project)
SELECT * FROM `my_dataset.INFORMATION_SCHEMA.TABLES`;

-- Unqualified: INFORMATION_SCHEMA.X — SCHEMATA only (per BQ docs;
-- other views require at least a dataset prefix).
SELECT * FROM INFORMATION_SCHEMA.SCHEMATA;
```

For SCHEMATA, BigQuery's canonical form uses a `region-X` anchor
(e.g. `region-us.INFORMATION_SCHEMA.SCHEMATA`); the emulator's
permissive regex accepts it and lists all datasets in the project.

## Examples

### Find every table in a dataset (dbt / Looker pattern)

```sql
SELECT table_name, table_type, is_insertable_into, creation_time
FROM `${PROJECT}.${DATASET}.INFORMATION_SCHEMA.TABLES`
WHERE table_type = 'BASE TABLE'
ORDER BY table_name;
```

### Discover the column schema of a table (Dataform pattern)

```sql
SELECT column_name, ordinal_position, data_type, is_nullable, is_partitioning_column
FROM `${PROJECT}.${DATASET}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'orders'
ORDER BY ordinal_position;
```

### Enumerate the partitions of a partitioned table

```sql
SELECT partition_id, total_rows, last_modified_time, storage_tier
FROM `${PROJECT}.${DATASET}.INFORMATION_SCHEMA.PARTITIONS`
WHERE table_name = 'events'
ORDER BY partition_id;
```

### Read a table's options (description, labels, partition filter)

```sql
SELECT option_name, option_type, option_value
FROM `${PROJECT}.${DATASET}.INFORMATION_SCHEMA.TABLE_OPTIONS`
WHERE table_name = 'orders';
```

### Inspect a view's body

```sql
SELECT table_name, view_definition, use_standard_sql
FROM `${PROJECT}.${DATASET}.INFORMATION_SCHEMA.VIEWS`
WHERE table_name = 'monthly_summary';
```

## Out of scope — `INFORMATION_SCHEMA.JOBS*`

The `JOBS` / `JOBS_BY_USER` / `JOBS_BY_PROJECT` / `JOBS_BY_FOLDER`
/ `JOBS_BY_ORGANIZATION` views are permanently
[out of scope](../reference/out-of-scope.md#information_schemajobs-family).
Job history in the emulator is in-memory only; the
`INFORMATION_SCHEMA.JOBS*` views are primarily billing/quota
observability surfaces and the emulator has no billing model.

Use the REST `jobs.list` endpoint instead:

```python
from google.cloud import bigquery
client = bigquery.Client(...)
for job in client.list_jobs(state_filter="DONE"):
    print(job.job_id, job.statement_type, job.total_bytes_processed)
```

## How it works under the hood

The emulator implements INFORMATION_SCHEMA via a pre-translation
rewriter ([`src/bqemulator/sql/rewriter/information_schema.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/information_schema.py))
that detects `INFORMATION_SCHEMA.<view>` references in the BQ SQL
input and replaces each with an inline `VALUES` subquery
materialised from the catalog. No DuckDB-side virtual tables are
exposed to user queries; the rewriter runs in the same translator
phase as the wildcard expander, before the BigQuery → DuckDB
transpile step.

This design keeps the implementation simple, makes the column
schema match BigQuery's documented contract byte-for-byte, and
avoids dragging DuckDB's native `information_schema` columns
(different shape, different types) into the BigQuery surface.

For `PARTITIONS`, the rewriter queries the catalog's
`list_partitions()` helper which in turn runs a `GROUP BY` on the
partitioning column against the table's physical DuckDB storage —
this is the one view that touches live row data instead of pure
catalog metadata.
