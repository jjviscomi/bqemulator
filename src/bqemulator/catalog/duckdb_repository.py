"""DuckDB-backed implementation of :class:`CatalogRepository`.

Stores metadata in tables under the reserved ``_bqemulator_catalog`` schema:

* ``_bqemulator_catalog.datasets`` — DatasetMeta (one row per dataset).
* ``_bqemulator_catalog.tables`` — TableMeta (one row per table).
* ``_bqemulator_catalog.routines`` — RoutineMeta.
* ``_bqemulator_catalog.jobs`` — JobMeta.
* ``_bqemulator_catalog.snapshots`` — SnapshotMeta.
* ``_bqemulator_catalog.materialized_views`` — MaterializedViewMeta.
* ``_bqemulator_catalog.row_access_policies`` — RowAccessPolicyMeta.

Full schema DDL lives in :mod:`bqemulator.catalog.migrations`.

The repository keeps a hot, read-only :class:`MemoryCatalogRepository`
cache that mirrors the on-disk rows. Every read is served from the cache
(O(1)); every mutation writes through to DuckDB *and* updates the cache
so concurrent readers see the new state immediately. On
:meth:`ensure_ready` the cache is hydrated by reading each catalog
table and reconstructing the Pydantic models from the JSON column.

Backup/restore and seed/export round-trip the catalog because every
entity lands in DuckDB.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.migrations import run_migrations
from bqemulator.catalog.models import (
    AccessEntry,
    DatasetMeta,
    JobMeta,
    MaterializedViewMeta,
    PartitionMeta,
    RoutineMeta,
    RowAccessPolicyMeta,
    SnapshotMeta,
    TableMeta,
)
from bqemulator.catalog.repository import CatalogRepository
from bqemulator.domain.errors import InternalError
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.engine import CATALOG_SCHEMA

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine

_log = get_logger(__name__)


class DuckDBCatalogRepository(CatalogRepository):
    """Catalog repository backed by DuckDB metadata tables.

    Reads go through an in-memory cache hydrated at startup; writes
    update both the cache and the underlying DuckDB tables so the
    persistent state matches the in-memory view at every step.
    """

    def __init__(self, engine: DuckDBEngine, *, lenient: bool = False) -> None:
        """Construct the repository.

        Args:
            engine: The shared DuckDB engine.
            lenient: When True, corruption in any single catalog row is
                logged at WARNING level and the row is skipped. When
                False (default), corruption raises
                :class:`~bqemulator.domain.errors.InternalError` with
                a message identifying the offending row so an operator
                can fix it. Lenient mode is intended for restore /
                disaster-recovery paths; production servers should
                fail fast.
        """
        self._engine = engine
        self._cache = MemoryCatalogRepository()
        self._loaded = False
        self._lenient = lenient

    def ensure_ready(self) -> None:
        """Run migrations and hydrate the in-memory cache from DuckDB."""
        if self._loaded:
            return
        run_migrations(self._engine)
        self._hydrate_from_duckdb()
        self._loaded = True

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    def _hydrate_from_duckdb(self) -> None:
        """Load every catalog table into the in-memory cache."""
        self._hydrate_datasets()
        self._hydrate_tables()
        self._hydrate_routines()
        self._hydrate_jobs()
        self._hydrate_snapshots()
        self._hydrate_materialized_views()
        self._hydrate_row_access_policies()
        _log.debug(
            "catalog.hydrate.done",
            datasets=len(self._cache._datasets),  # noqa: SLF001
            tables=len(self._cache._tables),  # noqa: SLF001
            routines=len(self._cache._routines),  # noqa: SLF001
        )

    def _surface_corruption(
        self,
        *,
        table: str,
        row_id: str,
        exc: Exception,
    ) -> None:
        """Raise (strict) or log+skip (lenient) when a catalog row is corrupt.

        The strict-mode error carries the offending row's identity AND
        the underlying exception so an operator can locate the bad row
        with a single SELECT. Lenient mode keeps the cache populated
        with whatever did parse — useful when restoring a partial
        backup and you'd rather have 99% of the catalog than 0%.
        """
        message = (
            f"Catalog corruption in {CATALOG_SCHEMA}.{table} row {row_id}: "
            f"{type(exc).__name__}: {exc}. Re-import or restore from a "
            "known-good backup. (Pass lenient=True to skip corrupt rows.)"
        )
        if not self._lenient:
            raise InternalError(message) from exc
        _log.warning(
            "catalog.hydrate.row_corrupt",
            table=table,
            row_id=row_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    def _hydrate_datasets(self) -> None:
        rows = self._engine.execute(
            f'SELECT project_id, dataset_id, metadata_json FROM "{CATALOG_SCHEMA}"."datasets"',
        ).fetchall()
        for project_id, dataset_id, raw in rows:
            row_id = f"{project_id}.{dataset_id}"
            try:
                meta = DatasetMeta.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001 — surface every corruption mode
                self._surface_corruption(table="datasets", row_id=row_id, exc=exc)
                continue
            self._cache._datasets[(meta.project_id, meta.dataset_id)] = meta  # noqa: SLF001

    def _hydrate_tables(self) -> None:
        rows = self._engine.execute(
            f"SELECT project_id, dataset_id, table_id, metadata_json "
            f'FROM "{CATALOG_SCHEMA}"."tables"',
        ).fetchall()
        for project_id, dataset_id, table_id, raw in rows:
            row_id = f"{project_id}.{dataset_id}.{table_id}"
            try:
                meta = TableMeta.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001
                self._surface_corruption(table="tables", row_id=row_id, exc=exc)
                continue
            self._cache._tables[(meta.project_id, meta.dataset_id, meta.table_id)] = meta  # noqa: SLF001

    def _hydrate_routines(self) -> None:
        rows = self._engine.execute(
            f"SELECT project_id, dataset_id, routine_id, metadata_json "
            f'FROM "{CATALOG_SCHEMA}"."routines"',
        ).fetchall()
        for project_id, dataset_id, routine_id, raw in rows:
            row_id = f"{project_id}.{dataset_id}.{routine_id}"
            try:
                meta = RoutineMeta.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001
                self._surface_corruption(table="routines", row_id=row_id, exc=exc)
                continue
            self._cache._routines[(meta.project_id, meta.dataset_id, meta.routine_id)] = meta  # noqa: SLF001

    def _hydrate_jobs(self) -> None:
        rows = self._engine.execute(
            f'SELECT project_id, job_id, metadata_json FROM "{CATALOG_SCHEMA}"."jobs"',
        ).fetchall()
        for project_id, job_id, raw in rows:
            row_id = f"{project_id}/{job_id}"
            try:
                meta = JobMeta.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001
                self._surface_corruption(table="jobs", row_id=row_id, exc=exc)
                continue
            self._cache._jobs[(meta.project_id, meta.job_id)] = meta  # noqa: SLF001

    def _hydrate_snapshots(self) -> None:
        rows = self._engine.execute(
            f"SELECT snapshot_id, project_id, dataset_id, table_id, snapshot_time, "
            f"kind, duckdb_schema, duckdb_table, expires_at "
            f'FROM "{CATALOG_SCHEMA}"."snapshots"',
        ).fetchall()
        for row in rows:
            row_id = str(row[0])
            try:
                meta = SnapshotMeta(
                    snapshot_id=row[0],
                    project_id=row[1],
                    dataset_id=row[2],
                    table_id=row[3],
                    snapshot_time=row[4],
                    kind=row[5],
                    duckdb_schema=row[6],
                    duckdb_table=row[7],
                    expires_at=row[8],
                )
            except Exception as exc:  # noqa: BLE001
                self._surface_corruption(table="snapshots", row_id=row_id, exc=exc)
                continue
            self._cache._snapshots[meta.snapshot_id] = meta  # noqa: SLF001

    def _hydrate_materialized_views(self) -> None:
        rows = self._engine.execute(
            f"SELECT mv.project_id, mv.dataset_id, mv.table_id, mv.view_query, "
            f"mv.last_refresh_time, mv.is_stale, "
            f"COALESCE(LIST((d.base_project_id, d.base_dataset_id, d.base_table_id)), []) "
            f'FROM "{CATALOG_SCHEMA}"."materialized_views" mv '
            f'LEFT JOIN "{CATALOG_SCHEMA}"."mv_dependencies" d '
            f"ON mv.project_id = d.mv_project_id "
            f"AND mv.dataset_id = d.mv_dataset_id "
            f"AND mv.table_id = d.mv_table_id "
            f"GROUP BY mv.project_id, mv.dataset_id, mv.table_id, "
            f"mv.view_query, mv.last_refresh_time, mv.is_stale",
        ).fetchall()
        for row in rows:
            row_id = f"{row[0]}.{row[1]}.{row[2]}"
            try:
                base_tables_raw = row[6] or []
                base_tables = tuple(
                    (str(b[0]), str(b[1]), str(b[2])) for b in base_tables_raw if b is not None
                )
                meta = MaterializedViewMeta(
                    project_id=row[0],
                    dataset_id=row[1],
                    table_id=row[2],
                    view_query=row[3],
                    last_refresh_time=row[4],
                    is_stale=bool(row[5]),
                    base_tables=base_tables,
                )
            except Exception as exc:  # noqa: BLE001
                self._surface_corruption(
                    table="materialized_views",
                    row_id=row_id,
                    exc=exc,
                )
                continue
            self._cache._mviews[(meta.project_id, meta.dataset_id, meta.table_id)] = meta  # noqa: SLF001

    def _hydrate_row_access_policies(self) -> None:
        rows = self._engine.execute(
            f"SELECT project_id, dataset_id, table_id, policy_id, filter_predicate, "
            f"grantees_json, creation_time, last_modified_time, etag "
            f'FROM "{CATALOG_SCHEMA}"."row_access_policies"',
        ).fetchall()
        for row in rows:
            row_id = f"{row[0]}.{row[1]}.{row[2]}/{row[3]}"
            try:
                grantees = tuple(json.loads(row[5]))
                meta = RowAccessPolicyMeta(
                    project_id=row[0],
                    dataset_id=row[1],
                    table_id=row[2],
                    policy_id=row[3],
                    filter_predicate=row[4],
                    grantees=grantees,
                    creation_time=row[6],
                    last_modified_time=row[7],
                    etag=row[8],
                )
            except Exception as exc:  # noqa: BLE001
                self._surface_corruption(
                    table="row_access_policies",
                    row_id=row_id,
                    exc=exc,
                )
                continue
            key = (meta.project_id, meta.dataset_id, meta.table_id, meta.policy_id)
            self._cache._row_access[key] = meta  # noqa: SLF001
        # Hydrate dataset access_entries onto the corresponding DatasetMeta.
        ds_entries: dict[tuple[str, str], list[AccessEntry]] = {}
        entry_rows = self._engine.execute(
            f"SELECT project_id, dataset_id, slot, role, user_by_email, group_by_email, "
            f"domain, special_group, iam_member, view_ref, routine_ref, dataset_ref "
            f'FROM "{CATALOG_SCHEMA}"."dataset_access_entries" '
            f"ORDER BY project_id, dataset_id, slot",
        ).fetchall()
        for er in entry_rows:
            row_id = f"{er[0]}.{er[1]}#{er[2]}"
            try:
                entry = _row_to_access_entry(er)
            except Exception as exc:  # noqa: BLE001
                self._surface_corruption(
                    table="dataset_access_entries",
                    row_id=row_id,
                    exc=exc,
                )
                continue
            ds_entries.setdefault((er[0], er[1]), []).append(entry)
        for (pid, did), entries in ds_entries.items():
            current = self._cache._datasets.get((pid, did))  # noqa: SLF001
            if current is None:
                continue
            self._cache._datasets[(pid, did)] = current.model_copy(  # noqa: SLF001
                update={"access_entries": tuple(entries)},
            )

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    def list_datasets(self, project_id: str) -> tuple[DatasetMeta, ...]:
        self.ensure_ready()
        return self._cache.list_datasets(project_id)

    def list_all_datasets(self) -> tuple[DatasetMeta, ...]:
        self.ensure_ready()
        return self._cache.list_all_datasets()

    def get_dataset(self, project_id: str, dataset_id: str) -> DatasetMeta | None:
        self.ensure_ready()
        return self._cache.get_dataset(project_id, dataset_id)

    def create_dataset(self, dataset: DatasetMeta) -> DatasetMeta:
        self.ensure_ready()
        created = self._cache.create_dataset(dataset)
        self._write_dataset(created)
        return created

    def update_dataset(self, dataset: DatasetMeta) -> DatasetMeta:
        self.ensure_ready()
        updated = self._cache.update_dataset(dataset)
        self._write_dataset(updated)
        return updated

    def delete_dataset(
        self,
        project_id: str,
        dataset_id: str,
        *,
        not_found_ok: bool = False,
        delete_contents: bool = False,
    ) -> None:
        self.ensure_ready()
        had = self._cache.get_dataset(project_id, dataset_id) is not None
        self._cache.delete_dataset(
            project_id,
            dataset_id,
            not_found_ok=not_found_ok,
            delete_contents=delete_contents,
        )
        if had:
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."datasets" '
                f"WHERE project_id = ? AND dataset_id = ?",
                [project_id, dataset_id],
            )
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."dataset_access_entries" '
                f"WHERE project_id = ? AND dataset_id = ?",
                [project_id, dataset_id],
            )
            if delete_contents:
                self._engine.execute(
                    f'DELETE FROM "{CATALOG_SCHEMA}"."tables" '
                    f"WHERE project_id = ? AND dataset_id = ?",
                    [project_id, dataset_id],
                )
                self._engine.execute(
                    f'DELETE FROM "{CATALOG_SCHEMA}"."routines" '
                    f"WHERE project_id = ? AND dataset_id = ?",
                    [project_id, dataset_id],
                )
                # Cascade-delete every dataset-scoped resource. Without
                # these, deleting+recreating the same ``(project, dataset)``
                # leaks snapshots, MVs, MV deps, and RAPs past the dataset
                # drop. The next REST POST against the same table returns
                # 409 Conflict because the orphaned row is still keyed by
                # the same triple. The in-memory cache cascade above
                # already handles its mirror; this block keeps the
                # persistent DuckDB-backed catalog in sync.
                self._engine.execute(
                    f'DELETE FROM "{CATALOG_SCHEMA}"."row_access_policies" '
                    f"WHERE project_id = ? AND dataset_id = ?",
                    [project_id, dataset_id],
                )
                self._engine.execute(
                    f'DELETE FROM "{CATALOG_SCHEMA}"."snapshots" '
                    f"WHERE project_id = ? AND dataset_id = ?",
                    [project_id, dataset_id],
                )
                self._engine.execute(
                    f'DELETE FROM "{CATALOG_SCHEMA}"."materialized_views" '
                    f"WHERE project_id = ? AND dataset_id = ?",
                    [project_id, dataset_id],
                )
                # ``mv_dependencies`` joins MVs to bases — drop edges from
                # both sides so a dataset that hosted a base table (not
                # the MV itself) also gets its incoming edges cleaned.
                self._engine.execute(
                    f'DELETE FROM "{CATALOG_SCHEMA}"."mv_dependencies" '
                    f"WHERE (mv_project_id = ? AND mv_dataset_id = ?) "
                    f"OR (base_project_id = ? AND base_dataset_id = ?)",
                    [project_id, dataset_id, project_id, dataset_id],
                )

    def _write_dataset(self, dataset: DatasetMeta) -> None:
        self._engine.execute(
            f'INSERT OR REPLACE INTO "{CATALOG_SCHEMA}"."datasets" '
            f"(project_id, dataset_id, metadata_json, creation_time, "
            f"last_modified_time, etag) VALUES (?, ?, ?, ?, ?, ?)",
            [
                dataset.project_id,
                dataset.dataset_id,
                dataset.model_dump_json(by_alias=True),
                dataset.creation_time,
                dataset.last_modified_time,
                dataset.etag,
            ],
        )
        # Re-write the access_entries side table.
        self._engine.execute(
            f'DELETE FROM "{CATALOG_SCHEMA}"."dataset_access_entries" '
            f"WHERE project_id = ? AND dataset_id = ?",
            [dataset.project_id, dataset.dataset_id],
        )
        for idx, entry in enumerate(dataset.access_entries):
            self._engine.execute(
                f'INSERT INTO "{CATALOG_SCHEMA}"."dataset_access_entries" '
                f"(project_id, dataset_id, slot, role, user_by_email, "
                f"group_by_email, domain, special_group, iam_member, "
                f"view_ref, routine_ref, dataset_ref) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    dataset.project_id,
                    dataset.dataset_id,
                    idx,
                    entry.role,
                    entry.user_by_email,
                    entry.group_by_email,
                    entry.domain,
                    entry.special_group,
                    entry.iam_member,
                    _join_ref(entry.view),
                    _join_ref(entry.routine),
                    _join_ref(entry.dataset),
                ],
            )

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def list_tables(self, project_id: str, dataset_id: str) -> tuple[TableMeta, ...]:
        self.ensure_ready()
        return self._cache.list_tables(project_id, dataset_id)

    def list_storage_tables(self, project_id: str, dataset_id: str) -> tuple[str, ...]:
        """Return every DuckDB table name in the dataset's physical schema.

        The catalog cache only tracks REST-registered tables. The
        wildcard-table expander needs every shard visible to DuckDB —
        including those created via ``CREATE TABLE … AS SELECT`` — so
        this method goes straight to DuckDB's ``information_schema``
        and pulls the table names out of the ``"project__dataset"``
        schema directly.
        """
        self.ensure_ready()
        schema = f"{project_id}__{dataset_id}"
        rows = self._engine.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
            [schema],
        ).fetchall()
        return tuple(row[0] for row in rows)

    def get_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> TableMeta | None:
        self.ensure_ready()
        return self._cache.get_table(project_id, dataset_id, table_id)

    def create_table(self, table: TableMeta) -> TableMeta:
        self.ensure_ready()
        created = self._cache.create_table(table)
        self._write_table(created)
        return created

    def update_table(self, table: TableMeta) -> TableMeta:
        self.ensure_ready()
        updated = self._cache.update_table(table)
        self._write_table(updated)
        return updated

    def delete_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        self.ensure_ready()
        had = self._cache.get_table(project_id, dataset_id, table_id) is not None
        self._cache.delete_table(
            project_id,
            dataset_id,
            table_id,
            not_found_ok=not_found_ok,
        )
        if had:
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."tables" '
                f"WHERE project_id = ? AND dataset_id = ? AND table_id = ?",
                [project_id, dataset_id, table_id],
            )

    def list_views(self, project_id: str, dataset_id: str) -> tuple[TableMeta, ...]:
        self.ensure_ready()
        return tuple(
            t for t in self._cache.list_tables(project_id, dataset_id) if t.table_type == "VIEW"
        )

    def list_partitions(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[PartitionMeta, ...]:
        self.ensure_ready()
        table = self._cache.get_table(project_id, dataset_id, table_id)
        if table is None:
            return ()
        from bqemulator.storage.partition_state import (
            list_partitions_for_table,
        )

        return list_partitions_for_table(self._engine, table)

    def _write_table(self, table: TableMeta) -> None:
        self._engine.execute(
            f'INSERT OR REPLACE INTO "{CATALOG_SCHEMA}"."tables" '
            f"(project_id, dataset_id, table_id, table_type, metadata_json, "
            f"creation_time, last_modified_time, num_rows, num_bytes, etag) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                table.project_id,
                table.dataset_id,
                table.table_id,
                table.table_type,
                table.model_dump_json(by_alias=True),
                table.creation_time,
                table.last_modified_time,
                table.num_rows,
                table.num_bytes,
                table.etag,
            ],
        )

    # ------------------------------------------------------------------
    # Routines
    # ------------------------------------------------------------------

    def list_routines(
        self,
        project_id: str,
        dataset_id: str,
    ) -> tuple[RoutineMeta, ...]:
        self.ensure_ready()
        return self._cache.list_routines(project_id, dataset_id)

    def get_routine(
        self,
        project_id: str,
        dataset_id: str,
        routine_id: str,
    ) -> RoutineMeta | None:
        self.ensure_ready()
        return self._cache.get_routine(project_id, dataset_id, routine_id)

    def create_routine(self, routine: RoutineMeta) -> RoutineMeta:
        self.ensure_ready()
        created = self._cache.create_routine(routine)
        self._write_routine(created)
        return created

    def update_routine(self, routine: RoutineMeta) -> RoutineMeta:
        self.ensure_ready()
        updated = self._cache.update_routine(routine)
        self._write_routine(updated)
        return updated

    def delete_routine(
        self,
        project_id: str,
        dataset_id: str,
        routine_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        self.ensure_ready()
        had = self._cache.get_routine(project_id, dataset_id, routine_id) is not None
        self._cache.delete_routine(
            project_id,
            dataset_id,
            routine_id,
            not_found_ok=not_found_ok,
        )
        if had:
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."routines" '
                f"WHERE project_id = ? AND dataset_id = ? AND routine_id = ?",
                [project_id, dataset_id, routine_id],
            )

    def _write_routine(self, routine: RoutineMeta) -> None:
        self._engine.execute(
            f'INSERT OR REPLACE INTO "{CATALOG_SCHEMA}"."routines" '
            f"(project_id, dataset_id, routine_id, routine_type, language, "
            f"metadata_json, creation_time, last_modified_time, etag) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                routine.project_id,
                routine.dataset_id,
                routine.routine_id,
                routine.routine_type,
                routine.language,
                routine.model_dump_json(by_alias=True),
                routine.creation_time,
                routine.last_modified_time,
                routine.etag,
            ],
        )

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def list_jobs(
        self,
        project_id: str,
        *,
        state_filter: str | None = None,
        max_results: int = 100,
    ) -> tuple[JobMeta, ...]:
        self.ensure_ready()
        return self._cache.list_jobs(
            project_id,
            state_filter=state_filter,
            max_results=max_results,
        )

    def get_job(self, project_id: str, job_id: str) -> JobMeta | None:
        self.ensure_ready()
        return self._cache.get_job(project_id, job_id)

    def upsert_job(self, job: JobMeta) -> JobMeta:
        self.ensure_ready()
        upserted = self._cache.upsert_job(job)
        self._write_job(upserted)
        return upserted

    def delete_job(
        self,
        project_id: str,
        job_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        self.ensure_ready()
        had = self._cache.get_job(project_id, job_id) is not None
        self._cache.delete_job(project_id, job_id, not_found_ok=not_found_ok)
        if had:
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."jobs" WHERE project_id = ? AND job_id = ?',
                [project_id, job_id],
            )

    def _write_job(self, job: JobMeta) -> None:
        self._engine.execute(
            f'INSERT OR REPLACE INTO "{CATALOG_SCHEMA}"."jobs" '
            f"(project_id, job_id, job_type, state, metadata_json, "
            f"creation_time, start_time, end_time, etag) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                job.project_id,
                job.job_id,
                job.job_type,
                job.state,
                job.model_dump_json(by_alias=True),
                job.creation_time,
                job.start_time,
                job.end_time,
                job.etag,
            ],
        )

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def list_snapshots_for_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[SnapshotMeta, ...]:
        self.ensure_ready()
        return self._cache.list_snapshots_for_table(project_id, dataset_id, table_id)

    def list_all_snapshots(self) -> tuple[SnapshotMeta, ...]:
        self.ensure_ready()
        return self._cache.list_all_snapshots()

    def create_snapshot(self, snapshot: SnapshotMeta) -> SnapshotMeta:
        self.ensure_ready()
        created = self._cache.create_snapshot(snapshot)
        self._engine.execute(
            f'INSERT OR REPLACE INTO "{CATALOG_SCHEMA}"."snapshots" '
            f"(snapshot_id, project_id, dataset_id, table_id, snapshot_time, "
            f"kind, duckdb_schema, duckdb_table, expires_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                created.snapshot_id,
                created.project_id,
                created.dataset_id,
                created.table_id,
                created.snapshot_time,
                created.kind,
                created.duckdb_schema,
                created.duckdb_table,
                created.expires_at,
            ],
        )
        return created

    def delete_snapshot(
        self,
        snapshot_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        self.ensure_ready()
        had = snapshot_id in self._cache._snapshots  # noqa: SLF001
        self._cache.delete_snapshot(snapshot_id, not_found_ok=not_found_ok)
        if had:
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."snapshots" WHERE snapshot_id = ?',
                [snapshot_id],
            )

    # ------------------------------------------------------------------
    # Materialized views
    # ------------------------------------------------------------------

    def list_materialized_views(
        self,
        project_id: str,
        dataset_id: str,
    ) -> tuple[MaterializedViewMeta, ...]:
        self.ensure_ready()
        return self._cache.list_materialized_views(project_id, dataset_id)

    def list_all_materialized_views(self) -> tuple[MaterializedViewMeta, ...]:
        self.ensure_ready()
        return self._cache.list_all_materialized_views()

    def get_materialized_view(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> MaterializedViewMeta | None:
        self.ensure_ready()
        return self._cache.get_materialized_view(project_id, dataset_id, table_id)

    def upsert_materialized_view(
        self,
        view: MaterializedViewMeta,
    ) -> MaterializedViewMeta:
        self.ensure_ready()
        upserted = self._cache.upsert_materialized_view(view)
        self._engine.execute(
            f'INSERT OR REPLACE INTO "{CATALOG_SCHEMA}"."materialized_views" '
            f"(project_id, dataset_id, table_id, view_query, "
            f"last_refresh_time, is_stale) VALUES (?, ?, ?, ?, ?, ?)",
            [
                upserted.project_id,
                upserted.dataset_id,
                upserted.table_id,
                upserted.view_query,
                upserted.last_refresh_time,
                upserted.is_stale,
            ],
        )
        # Rewrite dependency edges atomically.
        self._engine.execute(
            f'DELETE FROM "{CATALOG_SCHEMA}"."mv_dependencies" '
            f"WHERE mv_project_id = ? AND mv_dataset_id = ? AND mv_table_id = ?",
            [upserted.project_id, upserted.dataset_id, upserted.table_id],
        )
        for base in upserted.base_tables:
            self._engine.execute(
                f'INSERT INTO "{CATALOG_SCHEMA}"."mv_dependencies" '
                f"(mv_project_id, mv_dataset_id, mv_table_id, "
                f"base_project_id, base_dataset_id, base_table_id) "
                f"VALUES (?, ?, ?, ?, ?, ?)",
                [
                    upserted.project_id,
                    upserted.dataset_id,
                    upserted.table_id,
                    base[0],
                    base[1],
                    base[2],
                ],
            )
        return upserted

    def delete_materialized_view(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        self.ensure_ready()
        had = self._cache.get_materialized_view(project_id, dataset_id, table_id) is not None
        self._cache.delete_materialized_view(
            project_id,
            dataset_id,
            table_id,
            not_found_ok=not_found_ok,
        )
        if had:
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."materialized_views" '
                f"WHERE project_id = ? AND dataset_id = ? AND table_id = ?",
                [project_id, dataset_id, table_id],
            )
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."mv_dependencies" '
                f"WHERE mv_project_id = ? AND mv_dataset_id = ? AND mv_table_id = ?",
                [project_id, dataset_id, table_id],
            )

    # ------------------------------------------------------------------
    # Row access policies
    # ------------------------------------------------------------------

    def list_row_access_policies(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[RowAccessPolicyMeta, ...]:
        self.ensure_ready()
        return self._cache.list_row_access_policies(project_id, dataset_id, table_id)

    def list_all_row_access_policies(self) -> tuple[RowAccessPolicyMeta, ...]:
        self.ensure_ready()
        return self._cache.list_all_row_access_policies()

    def get_row_access_policy(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
    ) -> RowAccessPolicyMeta | None:
        self.ensure_ready()
        return self._cache.get_row_access_policy(
            project_id,
            dataset_id,
            table_id,
            policy_id,
        )

    def create_row_access_policy(
        self,
        policy: RowAccessPolicyMeta,
    ) -> RowAccessPolicyMeta:
        self.ensure_ready()
        created = self._cache.create_row_access_policy(policy)
        self._write_row_access_policy(created)
        return created

    def update_row_access_policy(
        self,
        policy: RowAccessPolicyMeta,
    ) -> RowAccessPolicyMeta:
        self.ensure_ready()
        updated = self._cache.update_row_access_policy(policy)
        self._write_row_access_policy(updated)
        return updated

    def delete_row_access_policy(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        self.ensure_ready()
        had = (
            self._cache.get_row_access_policy(
                project_id,
                dataset_id,
                table_id,
                policy_id,
            )
            is not None
        )
        self._cache.delete_row_access_policy(
            project_id,
            dataset_id,
            table_id,
            policy_id,
            not_found_ok=not_found_ok,
        )
        if had:
            self._engine.execute(
                f'DELETE FROM "{CATALOG_SCHEMA}"."row_access_policies" '
                f"WHERE project_id = ? AND dataset_id = ? "
                f"AND table_id = ? AND policy_id = ?",
                [project_id, dataset_id, table_id, policy_id],
            )

    def _write_row_access_policy(self, policy: RowAccessPolicyMeta) -> None:
        self._engine.execute(
            f'INSERT OR REPLACE INTO "{CATALOG_SCHEMA}"."row_access_policies" '
            f"(project_id, dataset_id, table_id, policy_id, filter_predicate, "
            f"grantees_json, creation_time, last_modified_time, etag) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                policy.project_id,
                policy.dataset_id,
                policy.table_id,
                policy.policy_id,
                policy.filter_predicate,
                json.dumps(list(policy.grantees)),
                policy.creation_time,
                policy.last_modified_time,
                policy.etag,
            ],
        )


_REF_SEP = "::"


def _join_ref(parts: tuple[str, ...] | None) -> str | None:
    """Encode an access-entry reference tuple as a single string."""
    if parts is None:
        return None
    return _REF_SEP.join(parts)


def _split_ref(raw: str | None, *, parts: int) -> tuple[str, ...] | None:
    """Decode a ``::``-joined reference string back into a tuple."""
    if raw is None:
        return None
    split = tuple(raw.split(_REF_SEP))
    return split if len(split) == parts else None


def _row_to_access_entry(row: tuple[Any, ...]) -> AccessEntry:
    """Reconstruct an AccessEntry from a ``dataset_access_entries`` row."""
    return AccessEntry(
        role=row[3],
        user_by_email=row[4],
        group_by_email=row[5],
        domain=row[6],
        special_group=row[7],
        iam_member=row[8],
        view=_split_ref(row[9], parts=3),  # type: ignore[arg-type]
        routine=_split_ref(row[10], parts=3),  # type: ignore[arg-type]
        dataset=_split_ref(row[11], parts=2),  # type: ignore[arg-type]
    )


__all__ = ["DuckDBCatalogRepository"]
