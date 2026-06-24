"""BigQuery ML model catalog schema (ADR 0047 / RFC 0002).

Adds the persistent ``_bqemulator_catalog.models`` table, keyed by
``(project_id, dataset_id, model_id)``. Rich fields live in
``metadata_json`` so they evolve without a migration; ``model_type`` and
``etag`` are promoted to dedicated columns, mirroring the ``routines``
table convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.storage.engine import CATALOG_SCHEMA

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine


__all__ = ["DESCRIPTION", "VERSION", "up"]

VERSION = 4
DESCRIPTION = "BigQuery ML models catalog table"


def up(engine: DuckDBEngine) -> None:
    """Create the models catalog table."""
    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."models" (
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            model_id VARCHAR NOT NULL,
            model_type VARCHAR NOT NULL,
            metadata_json VARCHAR NOT NULL,
            creation_time TIMESTAMP WITH TIME ZONE NOT NULL,
            last_modified_time TIMESTAMP WITH TIME ZONE NOT NULL,
            etag VARCHAR NOT NULL,
            PRIMARY KEY (project_id, dataset_id, model_id)
        )
        """,
    )
