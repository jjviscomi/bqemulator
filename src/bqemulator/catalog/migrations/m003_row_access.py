"""Row access policy + dataset access-entries catalog schema.

Adds two persistent tables under the reserved ``_bqemulator_catalog``
schema:

* ``_bqemulator_catalog.row_access_policies`` — one row per
  :class:`RowAccessPolicyMeta`. Identity is
  ``(project, dataset, table, policy)``; ``etag`` carries the
  optimistic-concurrency token returned in REST responses; ``grantees``
  is stored as a JSON array string so the ordering set by the client
  on insert is preserved through round-trips.
* ``_bqemulator_catalog.dataset_access_entries`` — one row per entry
  in a dataset's ``access`` array. The ``view`` / ``routine`` /
  ``dataset`` shapes are stored as the three-part qualified id (or
  two-part for ``dataset``) using a single composite VARCHAR — the
  serializer joins with ``::`` so empty parts can't be misparsed.

The DuckDB-backed catalog repository delegates to a memory cache,
exactly as it does for snapshots and materialized views, so these
tables are created at startup but write-through is implementation-
detail of the cache.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.storage.engine import CATALOG_SCHEMA

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine


__all__ = ["DESCRIPTION", "VERSION", "up"]

VERSION = 3
DESCRIPTION = "row access policy + dataset access-entries catalog tables"


def up(engine: DuckDBEngine) -> None:
    """Create the row access + dataset access-entries catalog tables."""
    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."row_access_policies" (
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            table_id VARCHAR NOT NULL,
            policy_id VARCHAR NOT NULL,
            filter_predicate VARCHAR NOT NULL,
            grantees_json VARCHAR NOT NULL,
            creation_time TIMESTAMP WITH TIME ZONE NOT NULL,
            last_modified_time TIMESTAMP WITH TIME ZONE NOT NULL,
            etag VARCHAR NOT NULL,
            PRIMARY KEY (project_id, dataset_id, table_id, policy_id)
        )
        """,
    )

    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."dataset_access_entries" (
            project_id VARCHAR NOT NULL,
            dataset_id VARCHAR NOT NULL,
            slot INTEGER NOT NULL,
            role VARCHAR,
            user_by_email VARCHAR,
            group_by_email VARCHAR,
            domain VARCHAR,
            special_group VARCHAR,
            iam_member VARCHAR,
            view_ref VARCHAR,
            routine_ref VARCHAR,
            dataset_ref VARCHAR,
            PRIMARY KEY (project_id, dataset_id, slot)
        )
        """,
    )
