"""Catalog — BigQuery-rich metadata (beyond DuckDB ``information_schema``).

The catalog stores metadata that DuckDB's native catalog cannot represent:
BigQuery-specific schema JSON, labels, partitioning/clustering
configurations, table descriptions, IAM policies (stored, not enforced),
row access policies (stored AND enforced), routines, job history, etc.

Two implementations of :class:`CatalogRepository` are provided:

* :class:`MemoryCatalogRepository` — backed by Python dicts. Unit tests.
* :class:`DuckDBCatalogRepository` — backed by tables in the reserved
  ``_bqemulator_catalog`` DuckDB schema. Production use.
"""

from __future__ import annotations

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    JobMeta,
    RoutineMeta,
    TableMeta,
)
from bqemulator.catalog.repository import CatalogRepository

__all__ = [
    "CatalogRepository",
    "DatasetMeta",
    "DuckDBCatalogRepository",
    "JobMeta",
    "MemoryCatalogRepository",
    "RoutineMeta",
    "TableMeta",
    "generate_etag",
]
