"""Initial catalog schema.

Creates empty catalog tables under the reserved ``_bqemulator_catalog``
schema. The tables use JSON columns for rich BigQuery-specific fields
(schema, labels, partitioning) so we can add fields without a migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.storage.engine import CATALOG_SCHEMA

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine

VERSION = 1
DESCRIPTION = "Initial catalog schema (datasets, tables, routines, jobs)"


def up(engine: DuckDBEngine) -> None:
    """Create the initial catalog tables."""
    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."datasets" (
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            metadata_json VARCHAR NOT NULL,
            creation_time TIMESTAMP WITH TIME ZONE NOT NULL,
            last_modified_time TIMESTAMP WITH TIME ZONE NOT NULL,
            etag VARCHAR NOT NULL,
            PRIMARY KEY (project_id, dataset_id)
        )
        """,
    )
    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."tables" (
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            table_id VARCHAR NOT NULL,
            table_type VARCHAR NOT NULL,
            metadata_json VARCHAR NOT NULL,
            creation_time TIMESTAMP WITH TIME ZONE NOT NULL,
            last_modified_time TIMESTAMP WITH TIME ZONE NOT NULL,
            num_rows BIGINT NOT NULL DEFAULT 0,
            num_bytes BIGINT NOT NULL DEFAULT 0,
            etag VARCHAR NOT NULL,
            PRIMARY KEY (project_id, dataset_id, table_id)
        )
        """,
    )
    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."routines" (
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            routine_id VARCHAR NOT NULL,
            routine_type VARCHAR NOT NULL,
            language VARCHAR NOT NULL,
            metadata_json VARCHAR NOT NULL,
            creation_time TIMESTAMP WITH TIME ZONE NOT NULL,
            last_modified_time TIMESTAMP WITH TIME ZONE NOT NULL,
            etag VARCHAR NOT NULL,
            PRIMARY KEY (project_id, dataset_id, routine_id)
        )
        """,
    )
    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."jobs" (
            project_id VARCHAR NOT NULL,
            job_id VARCHAR NOT NULL,
            job_type VARCHAR NOT NULL,
            state VARCHAR NOT NULL,
            metadata_json VARCHAR NOT NULL,
            creation_time TIMESTAMP WITH TIME ZONE NOT NULL,
            start_time TIMESTAMP WITH TIME ZONE,
            end_time TIMESTAMP WITH TIME ZONE,
            etag VARCHAR NOT NULL,
            PRIMARY KEY (project_id, job_id)
        )
        """,
    )
