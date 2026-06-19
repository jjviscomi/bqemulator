# ADR 0045: Autodetect Schema Inference via DuckDB

## Status

Accepted

## Context

BigQuery supports an `autodetect` flag for load jobs (CSV and JSON) that instructs the engine to infer the table schema from the data if a schema is not explicitly provided. The `bqemulator` previously treated a missing schema on a `CREATE_IF_NEEDED` load as a no-op during destination creation, relying on downstream components to raise binder errors, which caused a parity gap with BigQuery where the table would actually be created.

## Decision

We will implement schema auto-detection by leveraging DuckDB's native capabilities (`read_csv_auto` and `read_json_auto`).

1. **Sampling:** When `autodetect` is true and no schema is provided, the emulator will issue a `CREATE TABLE ... AS SELECT * FROM read_csv_auto/read_json_auto LIMIT 0` against the *first* source URI.
2. **Inference:** We will then use `DESCRIBE` to read the inferred column types from DuckDB.
3. **Type Mapping:** DuckDB types are mapped to BigQuery REST schema fields by `duckdb_type_to_bq_field`. Scalars use BigQuery's legacy wire type names (`INTEGER`, `FLOAT`, `BOOLEAN`, ...), matching what real BigQuery returns from `tables.get`.
4. **Nested Types:** Structural types map with full parity. A DuckDB `STRUCT` becomes a BigQuery `RECORD` whose fields are converted recursively; an array (`T[]` or `LIST(T)`) becomes the element's field with `mode=REPEATED`, so an array of struct becomes a `REPEATED` `RECORD` and an array of scalar a `REPEATED` scalar. An array of array, which BigQuery's schema model cannot represent, is rejected with a clear error.

## Consequences

- **Positive:** We close a known parity gap. CSV and JSON files automatically create tables with the correct schemas, including nested `RECORD` / `REPEATED` fields inferred from JSON objects and arrays, improving compatibility with pipelines that rely on BigQuery's autodetect feature. The inferred schema is verified against recorded real-BigQuery responses in the conformance corpus.
- **Negative (Limitations):**
  - Schema drift across multiple files in a single load job is not handled; we only infer from the first file.
  - An array of array cannot be represented in BigQuery's schema model and is rejected; provide an explicit schema for such data.
  - DuckDB and BigQuery do not guarantee the field order of an autodetected JSON schema, and the two orders can differ; the schema content (names, types, modes, nesting) is identical, and the conformance comparator matches schema fields by name.
