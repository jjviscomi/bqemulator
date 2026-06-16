# ADR 0045: Autodetect Schema Inference via DuckDB

## Status

Accepted

## Context

BigQuery supports an `autodetect` flag for load jobs (CSV and JSON) that instructs the engine to infer the table schema from the data if a schema is not explicitly provided. The `bqemulator` previously treated a missing schema on a `CREATE_IF_NEEDED` load as a no-op during destination creation, relying on downstream components to raise binder errors, which caused a parity gap with BigQuery where the table would actually be created.

## Decision

We will implement schema auto-detection by leveraging DuckDB's native capabilities (`read_csv_auto` and `read_json_auto`).

1. **Sampling:** When `autodetect` is true and no schema is provided, the emulator will issue a `CREATE TABLE ... AS SELECT * FROM read_csv_auto/read_json_auto LIMIT 0` against the *first* source URI.
2. **Inference:** We will then use `DESCRIBE` to read the inferred column types from DuckDB.
3. **Type Mapping:** DuckDB types are mapped to BigQuery types using our existing `duckdb_to_bq` mapper.
4. **Complex Type Fallback:** For deeply nested types (e.g., DuckDB `STRUCT` or `LIST` inferred from JSON), attempting to map these to BigQuery `RECORD` or `REPEATED` fields is brittle due to DuckDB's loose structural inference on variable JSON arrays. Therefore, we introduce a `strict=False` mode to `duckdb_to_bq` which explicitly maps these compound types to `STRING`.

## Consequences

- **Positive:** We close a known parity gap; basic CSV and JSON files will now automatically create tables with the correct flat schemas, improving compatibility with pipelines that rely on BigQuery's autodetect feature.
- **Negative (Limitations):**
  - Schema drift across multiple files in a single load job is not handled; we only infer from the first file.
  - Complex nested JSON structures will be imported as `STRING` rather than proper nested `RECORD`s. Users requiring strict nested types must provide an explicit schema.

