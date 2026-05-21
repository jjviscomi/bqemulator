"""``CREATE SNAPSHOT TABLE`` — user-visible immutable copies.

Unlike time-travel snapshots (which live in the reserved
``_bqemulator_snapshots`` schema and expire with retention), a user
``CREATE SNAPSHOT TABLE`` materialises a regular dataset table whose
``table_type`` is ``SNAPSHOT`` and whose rows are a point-in-time
copy of the source. The manager records a matching
:class:`SnapshotMeta` with ``kind=USER`` so GC does not touch it, and
tags the ``TableMeta`` with ``base_table`` and ``snapshot_time`` so
``tables.get`` exposes the provenance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import SnapshotMeta, TableMeta, TableSchema
from bqemulator.domain.errors import (
    InvalidQueryError,
    ResourceRef,
    resource_already_exists,
    resource_not_found,
)
from bqemulator.storage.sql_identifiers import quoted_table_ref, schema_name

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext


class SnapshotTableManager:
    """Handles ``CREATE SNAPSHOT TABLE`` and ``DROP SNAPSHOT TABLE``."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    async def create(
        self,
        target_project: str,
        target_dataset: str,
        target_table: str,
        source_project: str,
        source_dataset: str,
        source_table: str,
    ) -> TableMeta:
        """Materialise a ``SNAPSHOT`` table from the source."""
        src_meta = self._ctx.catalog.get_table(
            source_project,
            source_dataset,
            source_table,
        )
        if src_meta is None:
            raise resource_not_found(
                ResourceRef("table", source_project, source_dataset, source_table),
            )

        if (
            self._ctx.catalog.get_table(
                target_project,
                target_dataset,
                target_table,
            )
            is not None
        ):
            raise resource_already_exists(
                ResourceRef("table", target_project, target_dataset, target_table),
            )

        if self._ctx.catalog.get_dataset(target_project, target_dataset) is None:
            raise resource_not_found(
                ResourceRef("dataset", target_project, target_dataset),
            )

        src_ref = quoted_table_ref(source_project, source_dataset, source_table)
        dst_ref = quoted_table_ref(target_project, target_dataset, target_table)

        now = self._ctx.clock.now()

        async with self._ctx.engine.write_lock():
            self._ctx.engine.execute(
                f"CREATE TABLE {dst_ref} AS SELECT * FROM {src_ref}",
            )
            count_row = self._ctx.engine.execute(
                f"SELECT COUNT(*) FROM {dst_ref}",
            ).fetchone()
            num_rows = int(count_row[0]) if count_row else 0

            meta = TableMeta(
                project_id=target_project,
                dataset_id=target_dataset,
                table_id=target_table,
                table_type="SNAPSHOT",
                schema=(src_meta.schema_ or TableSchema()),
                labels={},
                time_partitioning=src_meta.time_partitioning,
                range_partitioning=src_meta.range_partitioning,
                clustering=src_meta.clustering,
                creation_time=now,
                last_modified_time=now,
                num_rows=num_rows,
                num_bytes=0,
                etag=generate_etag(
                    target_project,
                    target_dataset,
                    target_table,
                    "SNAPSHOT",
                    str(now),
                ),
                base_table=(f"{source_project}.{source_dataset}.{source_table}"),
                snapshot_time=now,
            )
            self._ctx.catalog.create_table(meta)

            # Also record a USER snapshot row so lookups on the source
            # table surface this as a persistent copy. ``duckdb_schema``
            # points at the *user-visible* schema (not the reserved
            # snapshots schema) because the physical table lives there.
            snapshot = SnapshotMeta(
                snapshot_id=(f"user__{target_project}__{target_dataset}__{target_table}"),
                project_id=source_project,
                dataset_id=source_dataset,
                table_id=source_table,
                snapshot_time=now,
                kind="USER",
                duckdb_schema=schema_name(target_project, target_dataset),
                duckdb_table=target_table,
                expires_at=None,
            )
            self._ctx.catalog.create_snapshot(snapshot)

        return meta

    async def drop(
        self,
        target_project: str,
        target_dataset: str,
        target_table: str,
    ) -> None:
        """Remove a user snapshot table and its catalog row."""
        existing = self._ctx.catalog.get_table(
            target_project,
            target_dataset,
            target_table,
        )
        if existing is None:
            raise resource_not_found(
                ResourceRef("table", target_project, target_dataset, target_table),
            )
        if existing.table_type != "SNAPSHOT":
            raise InvalidQueryError(
                f"Cannot DROP SNAPSHOT TABLE on table of type {existing.table_type}",
            )

        dst_ref = quoted_table_ref(target_project, target_dataset, target_table)
        snapshot_id = f"user__{target_project}__{target_dataset}__{target_table}"

        async with self._ctx.engine.write_lock():
            self._ctx.engine.execute(f"DROP TABLE IF EXISTS {dst_ref}")
            self._ctx.catalog.delete_snapshot(snapshot_id, not_found_ok=True)
            self._ctx.catalog.delete_table(
                target_project,
                target_dataset,
                target_table,
            )


__all__ = ["SnapshotTableManager"]
