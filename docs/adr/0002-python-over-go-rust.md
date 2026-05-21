# ADR 0002: Python over Go and Rust

- **Status**: Accepted

## Context

bqemulator is primarily a service with REST + gRPC surfaces. Any of
Python, Go, or Rust could ship it. The tie-breaker is the SQL translation
layer: **SQLGlot** is Python, and porting it would be a multi-person-year
effort.

## Decision

Python 3.11+. Direct in-process use of SQLGlot (no subprocess). Leverages
the vast Python data-engineering ecosystem (pytest, dbt, Airflow) for
example projects. Official `google-cloud-bigquery` Python client drives
integration tests.

## Consequences

- **Positive**: SQLGlot is a library import, not a subprocess; full AST
  access for custom transforms; pytest fixture is native.
- **Positive**: PyPI + prebuilt wheels for DuckDB, pyarrow, grpcio — users
  `pip install` without native compilation (goccy's ZetaSQL pain avoided).
- **Negative**: raw throughput below Go/Rust; acceptable for an emulator.
- **Negative**: GIL contention on hot paths; mitigated by DuckDB releasing
  the GIL during query execution and `asyncio.to_thread` for long queries.
