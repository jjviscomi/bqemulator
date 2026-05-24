"""CatalogRepository protocol.

Any implementation must honor the contracts documented on each method:

* ``get_*`` returns ``None`` when the resource does not exist. The API
  adapter is responsible for converting ``None`` to :class:`NotFoundError`.
* ``create_*`` raises :class:`AlreadyExistsError` when a resource with the
  same identity already exists.
* ``update_*`` raises :class:`NotFoundError` when the resource is absent.
* ``delete_*`` is idempotent when called with ``not_found_ok=True``; it
  otherwise raises :class:`NotFoundError`.
* ``list_*`` returns an empty tuple when no resources match; never ``None``.

Implementations must be safe to call from a single asyncio task at a time.
Concurrent writes should be gated by the caller (the storage engine's
write lock) — the repository itself does not serialize writes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from bqemulator.catalog.models import (
    DatasetMeta,
    JobMeta,
    MaterializedViewMeta,
    PartitionMeta,
    RoutineMeta,
    RowAccessPolicyMeta,
    SnapshotMeta,
    TableMeta,
)


@runtime_checkable
class CatalogRepository(Protocol):
    """Repository protocol for BigQuery-style metadata."""

    # -- Datasets ---------------------------------------------------------

    def list_datasets(self, project_id: str) -> tuple[DatasetMeta, ...]:
        """Return all datasets in ``project_id`` (possibly empty)."""
        ...

    def list_all_datasets(self) -> tuple[DatasetMeta, ...]:
        """Return every dataset across every project (possibly empty).

        Used by the admin / export / seed paths that need a full catalog
        walk without knowing the project ids in advance. Order is
        implementation-defined.
        """
        ...

    def get_dataset(self, project_id: str, dataset_id: str) -> DatasetMeta | None:
        """Return the dataset, or ``None`` if it does not exist."""
        ...

    def create_dataset(self, dataset: DatasetMeta) -> DatasetMeta:
        """Insert a new dataset. Raises AlreadyExistsError on conflict."""
        ...

    def update_dataset(self, dataset: DatasetMeta) -> DatasetMeta:
        """Replace an existing dataset. Raises NotFoundError if absent."""
        ...

    def delete_dataset(
        self,
        project_id: str,
        dataset_id: str,
        *,
        not_found_ok: bool = False,
        delete_contents: bool = False,
    ) -> None:
        """Delete a dataset. ``delete_contents`` cascades to tables/routines."""
        ...

    # -- Tables -----------------------------------------------------------

    def list_tables(self, project_id: str, dataset_id: str) -> tuple[TableMeta, ...]:
        """Return all tables in the dataset (possibly empty)."""
        ...

    def list_storage_tables(self, project_id: str, dataset_id: str) -> tuple[str, ...]:
        """Return table IDs physically present in storage for this dataset.

        Unlike :meth:`list_tables` (which returns BigQuery-level
        :class:`TableMeta` for catalog-registered tables only), this
        method also surfaces tables created directly via SQL DDL
        (``CREATE TABLE … AS SELECT``). The wildcard-table expander
        uses it so wildcard references engage on DDL-created shards
        the catalog cache hasn't been notified about.

        Returns table IDs without metadata; order is
        implementation-defined. Returns an empty tuple if no tables
        exist in the dataset.
        """
        ...

    def get_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> TableMeta | None:
        """Return the table, or ``None`` if it does not exist."""
        ...

    def create_table(self, table: TableMeta) -> TableMeta:
        """Insert a new table. Raises AlreadyExistsError on conflict."""
        ...

    def update_table(self, table: TableMeta) -> TableMeta:
        """Replace an existing table. Raises NotFoundError if absent."""
        ...

    def delete_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        """Delete a table."""
        ...

    def list_views(self, project_id: str, dataset_id: str) -> tuple[TableMeta, ...]:
        """Return all VIEW-typed tables in the dataset (possibly empty).

        Backs ``INFORMATION_SCHEMA.VIEWS``. The returned
        :class:`TableMeta` instances carry ``table_type='VIEW'`` and
        ``view_query`` populated with the BigQuery SQL view definition.
        """
        ...

    def list_partitions(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[PartitionMeta, ...]:
        """Return the distinct partitions for a (possibly partitioned) table.

        For a time-partitioned table this enumerates the distinct
        partition-grain values (``YYYYMMDD`` for DAY, ``YYYYMMDDHH`` for
        HOUR, etc.) plus row counts per partition. For an integer-range
        partitioned table the bucket starts are stringified
        (``"0"``, ``"100"``, …). For an unpartitioned table the entire
        table is treated as a single partition with
        ``partition_id='__NULL__'`` (BigQuery's documented sentinel).

        Implementations that have a live DuckDB engine query the
        physical storage; in-memory unit tests without a wired engine
        return an empty tuple.
        """
        ...

    # -- Routines ---------------------------------------------------------

    def list_routines(self, project_id: str, dataset_id: str) -> tuple[RoutineMeta, ...]:
        """Return all routines in the dataset."""
        ...

    def get_routine(
        self,
        project_id: str,
        dataset_id: str,
        routine_id: str,
    ) -> RoutineMeta | None:
        """Return the routine or ``None``."""
        ...

    def create_routine(self, routine: RoutineMeta) -> RoutineMeta:
        """Insert a new routine."""
        ...

    def update_routine(self, routine: RoutineMeta) -> RoutineMeta:
        """Replace an existing routine."""
        ...

    def delete_routine(
        self,
        project_id: str,
        dataset_id: str,
        routine_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        """Delete a routine."""
        ...

    # -- Jobs -------------------------------------------------------------

    def list_jobs(
        self,
        project_id: str,
        *,
        state_filter: str | None = None,
        max_results: int = 100,
    ) -> tuple[JobMeta, ...]:
        """Return recent jobs for the project."""
        ...

    def get_job(self, project_id: str, job_id: str) -> JobMeta | None:
        """Return the job or ``None``."""
        ...

    def upsert_job(self, job: JobMeta) -> JobMeta:
        """Insert a new job or replace the existing state for the same id."""
        ...

    def delete_job(
        self,
        project_id: str,
        job_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        """Delete a job record (metadata only; job results handled separately)."""
        ...

    # -- Snapshots ---------------------------------------------

    def list_snapshots_for_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[SnapshotMeta, ...]:
        """Return all snapshots for a base table ordered by ``snapshot_time``."""
        ...

    def list_all_snapshots(self) -> tuple[SnapshotMeta, ...]:
        """Return every snapshot known to the catalog."""
        ...

    def create_snapshot(self, snapshot: SnapshotMeta) -> SnapshotMeta:
        """Insert a new snapshot metadata entry."""
        ...

    def delete_snapshot(
        self,
        snapshot_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        """Delete a snapshot metadata entry by id."""
        ...

    # -- Materialized views ------------------------------------

    def list_materialized_views(
        self,
        project_id: str,
        dataset_id: str,
    ) -> tuple[MaterializedViewMeta, ...]:
        """Return all materialized views in a dataset."""
        ...

    def list_all_materialized_views(self) -> tuple[MaterializedViewMeta, ...]:
        """Return every materialized view known to the catalog."""
        ...

    def get_materialized_view(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> MaterializedViewMeta | None:
        """Return the MV entry or ``None``."""
        ...

    def upsert_materialized_view(
        self,
        view: MaterializedViewMeta,
    ) -> MaterializedViewMeta:
        """Insert or replace a materialized view entry."""
        ...

    def delete_materialized_view(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        """Delete a materialized view entry."""
        ...

    # -- Row access policies ----------------------------------

    def list_row_access_policies(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[RowAccessPolicyMeta, ...]:
        """Return all row access policies on the table (possibly empty)."""
        ...

    def list_all_row_access_policies(self) -> tuple[RowAccessPolicyMeta, ...]:
        """Return every row access policy known to the catalog."""
        ...

    def get_row_access_policy(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
    ) -> RowAccessPolicyMeta | None:
        """Return a single policy or ``None``."""
        ...

    def create_row_access_policy(
        self,
        policy: RowAccessPolicyMeta,
    ) -> RowAccessPolicyMeta:
        """Insert a new row access policy. Raises AlreadyExistsError on conflict."""
        ...

    def update_row_access_policy(
        self,
        policy: RowAccessPolicyMeta,
    ) -> RowAccessPolicyMeta:
        """Replace an existing row access policy. Raises NotFoundError if absent."""
        ...

    def delete_row_access_policy(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        """Delete a row access policy."""
        ...


__all__ = ["CatalogRepository"]
