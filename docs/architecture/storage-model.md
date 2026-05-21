# Storage model

## DuckDB layout

- Single DuckDB database (`:memory:` in ephemeral mode, file in
  persistent / import modes).
- Each BigQuery dataset maps to a DuckDB schema named
  `{project_id}__{dataset_id}` (double-underscore separator).
- Each BigQuery table maps to a DuckDB table with the same name inside
  the dataset's schema.
- The reserved schema `_bqemulator_catalog` holds emulator metadata
  (see [ADR 0006](../adr/0006-catalog-in-reserved-schema.md)).
- The reserved schema `_bqemulator_snapshots` holds time-travel
  snapshots (see [ADR 0009](../adr/0009-snapshot-layer-time-travel.md)).

## Catalog tables

Defined in `src/bqemulator/catalog/migrations/m001_initial.py`. Rich
BigQuery fields (schema, labels, partitioning, clustering) are stored in
`metadata_json` VARCHAR columns so the catalog schema stays stable across
BigQuery additions.

## Persistence modes

| Mode | DuckDB path | Use case |
|---|---|---|
| `ephemeral` | `:memory:` | CI, pytest fixture |
| `persistent` | `{data_dir}/bqemulator.duckdb` | Long-running dev server |
| `import` | same + schema sync from real project | Offline replica |

## Concurrency

DuckDB allows one writer at a time. `DuckDBEngine` wraps writes in an
`asyncio.Lock`. Reads bypass the lock (DuckDB handles read concurrency
internally for the same connection).

## Migrations

See `src/bqemulator/catalog/migrations/__init__.py`. Numbered modules
`mNNN_*.py` are discovered and applied in order; `_schema_version`
tracks which have been applied.
