"""BigQuery ML model catalog schema.

Adds the persistent ``_bqemulator_catalog.models`` table — one row per
:class:`~bqemulator.catalog.models.ModelMeta`. Identity is
``(project_id, dataset_id, model_id)``. The rich model fields
(feature/label column shapes, labels, encryption, training-query
provenance) live in the ``metadata_json`` column so they round-trip
through JSON without a schema change; ``model_type`` and ``etag`` are
promoted to dedicated columns to match the indexed-column convention
used by the ``routines`` table.

The DuckDB-backed catalog repository delegates reads to its in-memory
cache and writes through to this table, exactly as it does for
routines. See ADR 0047 / RFC 0002 for the surface-only BigQuery ML scope.
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
