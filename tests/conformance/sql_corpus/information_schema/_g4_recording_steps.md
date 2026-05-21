# G4 INFORMATION_SCHEMA fixture recording

The 18 SQL fixtures in this directory were authored as part of
workstream **G4 (2026-05-21)**, which extended the emulator's
INFORMATION_SCHEMA rewriter to cover `SCHEMATA`, `TABLES`, `COLUMNS`,
`TABLE_OPTIONS`, `VIEWS`, and `PARTITIONS` on top of the
Phase 6/7/8 `ROUTINES`, `MATERIALIZED_VIEWS`, and
`ROW_ACCESS_POLICIES` views.

Each fixture is shipped without an `expected.json` payload because
INFORMATION_SCHEMA recording must run against real BigQuery — the
exact column ordering, timestamp formatting, and option-value
serialisation are not derivable from the BigQuery public docs and
the recorder is the single source of truth.

## Why these aren't pre-recorded

INFORMATION_SCHEMA outputs include columns that vary by recording
context:

* `creation_time` / `last_modified_time` — wall-clock timestamps the
  emulator's deterministic clock can't reproduce row-for-row.
* `ddl` — BigQuery generates the canonical DDL string from the live
  catalog state at query time, including whitespace and quoting
  decisions that aren't documented as a stable contract.
* `data_type` — BigQuery's documented data-type string format
  generally matches our rewriter but the precise spelling of
  parameterised types (`NUMERIC(38, 9)` vs `NUMERIC`) varies.

Each `query.sql` is deliberately written to project only the
*deterministic* columns (e.g. `table_name`, `partition_id`,
`ordinal_position`, `option_name`, `option_type`) so the recorded
fixture is stable.

## Recording procedure

1. Make sure the operator has BigQuery ADC set up:

   ```bash
   gcloud auth application-default login
   export BQEMU_CONFORMANCE_PROJECT="your-bq-project"
   ```

2. Run the recorder against the G4 fixtures:

   ```bash
   make record-conformance ARGS="--filter information_schema/"
   ```

3. The recorder will:
   - Create an isolated dataset per fixture (named
     `bqemu_conformance_<hash>_<fixture_name>`).
   - Run the fixture's `setup.sql` against real BigQuery.
   - Run the fixture's `query.sql` and capture the result envelope.
   - Write `expected.json` in this directory.

4. Re-run the runner to confirm the fixtures pass:

   ```bash
   make test-conformance ARGS="-k information_schema"
   ```

## Fixture index

| Fixture | View | What it exercises |
|---|---|---|
| `is_schemata_basic` | SCHEMATA | dataset list with `region-us.INFORMATION_SCHEMA.SCHEMATA` |
| `is_schemata_with_filter` | SCHEMATA | filter by `schema_name = <dataset_id>` |
| `is_schemata_empty_project` | SCHEMATA | empty result for non-existent dataset name |
| `is_tables_basic` | TABLES | two-table dataset, deterministic ordering |
| `is_tables_filter_by_type` | TABLES | `WHERE table_type = 'BASE TABLE'` excludes views |
| `is_tables_select_specific_columns` | TABLES | `WHERE table_name = 'events'` filter |
| `is_columns_basic` | COLUMNS | three-column table, `ordinal_position` order |
| `is_columns_with_struct_field` | COLUMNS | STRUCT + ARRAY type rendering |
| `is_columns_partitioning_column` | COLUMNS | `is_partitioning_column='YES'` for partition col |
| `is_table_options_basic` | TABLE_OPTIONS | description option only |
| `is_table_options_description` | TABLE_OPTIONS | description value with embedded quotes |
| `is_table_options_partition_filter` | TABLE_OPTIONS | `require_partition_filter` BOOL option |
| `is_views_basic` | VIEWS | one view in the dataset |
| `is_views_with_definition` | VIEWS | view filter by name |
| `is_views_empty_dataset` | VIEWS | count of zero views |
| `is_partitions_basic` | PARTITIONS | DAY-partitioned table with two partitions |
| `is_partitions_ingestion_time` | PARTITIONS | `_PARTITIONDATE`-partitioned table |
| `is_partitions_empty_table` | PARTITIONS | partition count for empty partitioned table |

## Out of scope — INFORMATION_SCHEMA.JOBS family

The `JOBS`, `JOBS_BY_USER`, `JOBS_BY_PROJECT`, `JOBS_BY_FOLDER`, and
`JOBS_BY_ORGANIZATION` views are documented permanently out of
scope at
[`docs/reference/out-of-scope.md#information_schemajobs-family`](../../../docs/reference/out-of-scope.md#information_schemajobs-family).
The emulator does not model the BigQuery billing/quota subsystem;
querying job history through `INFORMATION_SCHEMA.JOBS` is a
billing-observability concern, not a SQL-semantics one. The REST
`jobs.list` endpoint is shipped and returns the equivalent metadata.
