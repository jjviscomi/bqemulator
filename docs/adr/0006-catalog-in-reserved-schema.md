# ADR 0006: Catalog co-located in a reserved DuckDB schema

- **Status**: Accepted

## Context

BigQuery table metadata is richer than DuckDB's `information_schema`:
labels, partitioning config, clustering config, descriptions, IAM
policies, row access policies, etc. We need somewhere to store it.

## Decision

Use tables in a reserved DuckDB schema named `_bqemulator_catalog` inside
the same DuckDB file as user data. User datasets cannot collide with this
name (validated at dataset creation).

Rich BigQuery-specific fields (schema, labels, partitioning, clustering)
are stored as JSON columns, keeping the catalog schema stable as BigQuery
evolves.

Migrations are numbered modules under
`bqemulator.catalog.migrations/mNNN_*.py`, tracked via a
`_bqemulator_catalog._schema_version` table.

## Consequences

- **Positive**: one DuckDB file to back up, restore, and ship.
- **Positive**: transactional coordination — catalog updates and user-data
  updates can be in the same DuckDB transaction.
- **Negative**: schema name is "reserved" — a user cannot create a dataset
  called `_bqemulator_catalog`. Validated and documented.
