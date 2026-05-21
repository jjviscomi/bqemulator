"""``CREATE TABLE ... CLONE`` — physical copy with clone table-type.

BigQuery clones share storage with the source until the first DML
against the clone, then diverge. DuckDB has no copy-on-write primitive,
so we do a physical ``CREATE TABLE AS SELECT`` and label the resulting
table with ``table_type=CLONE`` + ``base_table`` so REST responses and
``tables.list`` distinguish clones from regular tables. The
user-observable semantics match BigQuery: writes to the clone never
touch the source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import TableMeta, TableSchema
from bqemulator.domain.errors import (
    ResourceRef,
    resource_already_exists,
    resource_not_found,
)
from bqemulator.storage.sql_identifiers import quoted_table_ref

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext


class CloneManager:
    """Handles ``CREATE TABLE ... CLONE`` DDL."""

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
        """Materialise a clone and register it in the catalog."""
        src_meta = self._ctx.catalog.get_table(
            source_project,
            source_dataset,
            source_table,
        )
        if src_meta is None:
            raise resource_not_found(
                ResourceRef("table", source_project, source_dataset, source_table),
            )

        dst_meta = self._ctx.catalog.get_table(
            target_project,
            target_dataset,
            target_table,
        )
        if dst_meta is not None:
            raise resource_already_exists(
                ResourceRef("table", target_project, target_dataset, target_table),
            )

        # Ensure the destination dataset exists; clones land inside the
        # dataset's regular schema just like normal tables.
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

            # Preserve the source's schema, partitioning, and clustering.
            meta = TableMeta(
                project_id=target_project,
                dataset_id=target_dataset,
                table_id=target_table,
                table_type="CLONE",
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
                    "CLONE",
                    str(now),
                ),
                base_table=(f"{source_project}.{source_dataset}.{source_table}"),
            )
            self._ctx.catalog.create_table(meta)

        return meta


__all__ = ["CloneManager"]
