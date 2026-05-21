"""Phase 7 — versioning catalog schema.

Adds three persistent tables:

* ``_bqemulator_catalog.snapshots`` — metadata for every time-travel
  snapshot captured in the reserved ``_bqemulator_snapshots`` schema,
  plus ``CREATE SNAPSHOT TABLE`` entries materialised inside regular
  dataset schemas.
* ``_bqemulator_catalog.materialized_views`` — query source + staleness
  bookkeeping for every ``CREATE MATERIALIZED VIEW``. The view's
  physical rows live in the dataset schema under the regular ``tables``
  entry; this side table carries the additional refresh-relevant fields.
* ``_bqemulator_catalog.mv_dependencies`` — many-to-many edges between
  materialized views and their base tables, rebuilt on each
  ``CREATE / REFRESH`` so the event-bus subscriptions stay consistent
  with the catalog.

Also creates the ``_bqemulator_snapshots`` DuckDB schema where
snapshot tables live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.storage.engine import CATALOG_SCHEMA, SNAPSHOTS_SCHEMA

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine


# Re-export for modules that imported the name from here before the
# engine became the canonical home.
__all__ = ["DESCRIPTION", "SNAPSHOTS_SCHEMA", "VERSION", "up"]

VERSION = 2
DESCRIPTION = "Phase 7 — snapshot + materialized-view catalog tables"


def up(engine: DuckDBEngine) -> None:
    """Create the Phase 7 catalog tables and snapshots schema."""
    engine.execute(f'CREATE SCHEMA IF NOT EXISTS "{SNAPSHOTS_SCHEMA}"')

    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."snapshots" (
            snapshot_id VARCHAR PRIMARY KEY,
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            table_id VARCHAR NOT NULL,
            snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL,
            kind VARCHAR NOT NULL,
            duckdb_schema VARCHAR NOT NULL,
            duckdb_table VARCHAR NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE
        )
        """,
    )

    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."materialized_views" (
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            table_id VARCHAR NOT NULL,
            view_query VARCHAR NOT NULL,
            last_refresh_time TIMESTAMP WITH TIME ZONE NOT NULL,
            is_stale BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (project_id, dataset_id, table_id)
        )
        """,
    )

    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."mv_dependencies" (
            mv_project_id VARCHAR NOT NULL,
            mv_dataset_id VARCHAR NOT NULL,
            mv_table_id VARCHAR NOT NULL,
            base_project_id VARCHAR NOT NULL,
            base_dataset_id VARCHAR NOT NULL,
            base_table_id VARCHAR NOT NULL,
            PRIMARY KEY (
                mv_project_id,
                mv_dataset_id,
                mv_table_id,
                base_project_id,
                base_dataset_id,
                base_table_id
            )
        )
        """,
    )
