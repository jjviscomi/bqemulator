"""In-memory implementation of :class:`CatalogRepository`.

Used by unit tests and the default ephemeral mode. Thread/task-safe only
when the caller serializes writes; reads are safe because dict lookups on
CPython are atomic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bqemulator.catalog.models import (
    DatasetMeta,
    JobMeta,
    MaterializedViewMeta,
    ModelMeta,
    PartitionMeta,
    RoutineMeta,
    RowAccessPolicyMeta,
    SnapshotMeta,
    TableMeta,
)
from bqemulator.catalog.repository import CatalogRepository
from bqemulator.domain.errors import (
    NotFoundError,
    ResourceRef,
    resource_already_exists,
    resource_not_found,
)

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine


class MemoryCatalogRepository(CatalogRepository):
    """Dict-backed implementation of the catalog.

    Accepts an optional ``engine`` reference so the ephemeral-mode
    server (which wires this repo on top of a live DuckDB engine) can
    answer :meth:`list_storage_tables` from DuckDB's
    ``information_schema`` — surfacing tables created via SQL DDL that
    the in-memory cache never sees. When ``engine`` is ``None`` (the
    unit-test default) :meth:`list_storage_tables` falls back to the
    cached :class:`TableMeta` ids and the repo behaves as a pure
    in-memory store.
    """

    def __init__(self, engine: DuckDBEngine | None = None) -> None:
        self._datasets: dict[tuple[str, str], DatasetMeta] = {}
        self._tables: dict[tuple[str, str, str], TableMeta] = {}
        self._routines: dict[tuple[str, str, str], RoutineMeta] = {}
        self._models: dict[tuple[str, str, str], ModelMeta] = {}
        self._jobs: dict[tuple[str, str], JobMeta] = {}
        self._snapshots: dict[str, SnapshotMeta] = {}
        self._mviews: dict[tuple[str, str, str], MaterializedViewMeta] = {}
        self._row_access: dict[
            tuple[str, str, str, str],
            RowAccessPolicyMeta,
        ] = {}
        self._engine = engine

    # -- Datasets ---------------------------------------------------------

    def list_datasets(self, project_id: str) -> tuple[DatasetMeta, ...]:
        return tuple(d for (p, _d), d in self._datasets.items() if p == project_id)

    def list_all_datasets(self) -> tuple[DatasetMeta, ...]:
        return tuple(self._datasets.values())

    def get_dataset(self, project_id: str, dataset_id: str) -> DatasetMeta | None:
        return self._datasets.get((project_id, dataset_id))

    def create_dataset(self, dataset: DatasetMeta) -> DatasetMeta:
        key = (dataset.project_id, dataset.dataset_id)
        if key in self._datasets:
            raise resource_already_exists(
                ResourceRef("dataset", dataset.project_id, dataset.dataset_id),
            )
        self._datasets[key] = dataset
        return dataset

    def update_dataset(self, dataset: DatasetMeta) -> DatasetMeta:
        key = (dataset.project_id, dataset.dataset_id)
        if key not in self._datasets:
            raise resource_not_found(
                ResourceRef("dataset", dataset.project_id, dataset.dataset_id),
            )
        self._datasets[key] = dataset
        return dataset

    def delete_dataset(
        self,
        project_id: str,
        dataset_id: str,
        *,
        not_found_ok: bool = False,
        delete_contents: bool = False,
    ) -> None:
        key = (project_id, dataset_id)
        if key not in self._datasets:
            if not_found_ok:
                return
            raise resource_not_found(ResourceRef("dataset", project_id, dataset_id))

        # Contents check — block the drop unless caller opted into
        # cascade. Tables, routines, and models count toward "non-empty"
        # for this gate because they are top-level, user-visible dataset
        # children (a bare ``bq rm`` / ``deleteContents=false`` fails on
        # any of them in real BigQuery). Row-access policies / mviews /
        # snapshots cascade silently because BigQuery does not surface
        # them as blocking children.
        table_keys = self._keys_in_dataset(self._tables, project_id, dataset_id)
        routine_keys = self._keys_in_dataset(self._routines, project_id, dataset_id)
        model_keys = self._keys_in_dataset(self._models, project_id, dataset_id)
        if (table_keys or routine_keys or model_keys) and not delete_contents:
            raise NotFoundError(
                f"Dataset {project_id}.{dataset_id} is not empty; "
                "use delete_contents=True to cascade.",
            )

        # Cascade-delete every dataset-scoped resource. Row access policies,
        # materialized views, and snapshots all reference the dataset by
        # ``(project_id, dataset_id)``; without explicit cleanup they leak
        # past the dataset drop and resurface on the next ``create`` of the
        # same table id (POST /rowAccessPolicies returns 409 Conflict
        # because the previous policy is still keyed by the same triple).
        # The DuckDB-backed cascade (``DROP SCHEMA … CASCADE`` in the REST
        # route) only takes care of the DuckDB-side physical tables; this
        # in-memory catalog mirror needs its own cascade.
        self._delete_keys(self._tables, table_keys)
        self._delete_keys(self._routines, routine_keys)
        self._delete_keys(self._models, model_keys)
        self._delete_keys(
            self._row_access,
            self._keys_in_dataset(self._row_access, project_id, dataset_id),
        )
        self._delete_keys(
            self._mviews,
            self._keys_in_dataset(self._mviews, project_id, dataset_id),
        )
        self._purge_snapshots_in_dataset(project_id, dataset_id)
        del self._datasets[key]

    @staticmethod
    def _keys_in_dataset(
        mapping: dict[Any, Any],
        project_id: str,
        dataset_id: str,
    ) -> list[Any]:
        """Return all keys in ``mapping`` whose first two elements are ``(project, dataset)``.

        Works uniformly across the dataset-scoped tuple-keyed dicts
        (``_tables``, ``_routines``, ``_row_access``, ``_mviews``) —
        each uses a tuple starting with ``(project_id, dataset_id)``,
        only the trailing element differs.
        """
        return [k for k in mapping if k[0] == project_id and k[1] == dataset_id]

    @staticmethod
    def _delete_keys(mapping: dict[Any, Any], keys: list[Any]) -> None:
        """Drop each key from ``mapping`` in place. No-op for an empty list."""
        for k in keys:
            del mapping[k]

    def _purge_snapshots_in_dataset(self, project_id: str, dataset_id: str) -> None:
        """Delete snapshots referencing ``(project, dataset)``.

        Separate from ``_delete_keys`` because snapshot lookup is by
        snapshot id (not a (project, dataset, name) tuple) and the
        dataset reference lives on the value, not the key.
        """
        snapshot_ids = [
            sid
            for sid, s in self._snapshots.items()
            if s.project_id == project_id and s.dataset_id == dataset_id
        ]
        for sid in snapshot_ids:
            del self._snapshots[sid]

    # -- Tables -----------------------------------------------------------

    def list_tables(self, project_id: str, dataset_id: str) -> tuple[TableMeta, ...]:
        return tuple(
            t for (p, d, _t), t in self._tables.items() if p == project_id and d == dataset_id
        )

    def list_storage_tables(self, project_id: str, dataset_id: str) -> tuple[str, ...]:
        """Return table IDs visible in storage.

        When an engine was supplied at construction (ephemeral-mode
        server), this introspects DuckDB's ``information_schema`` so
        DDL-created tables surface for the wildcard expander. With no
        engine (the unit-test path), it returns the in-memory cached
        ids — a non-empty cache is the test's responsibility.
        """
        if self._engine is not None:
            schema = f"{project_id}__{dataset_id}"
            rows = self._engine.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                [schema],
            ).fetchall()
            return tuple(row[0] for row in rows)
        return tuple(t for (p, d, t) in self._tables if p == project_id and d == dataset_id)

    def get_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> TableMeta | None:
        return self._tables.get((project_id, dataset_id, table_id))

    def create_table(self, table: TableMeta) -> TableMeta:
        key = (table.project_id, table.dataset_id, table.table_id)
        if key in self._tables:
            raise resource_already_exists(
                ResourceRef("table", table.project_id, table.dataset_id, table.table_id),
            )
        # Verify parent dataset exists
        if (table.project_id, table.dataset_id) not in self._datasets:
            raise resource_not_found(
                ResourceRef("dataset", table.project_id, table.dataset_id),
            )
        self._tables[key] = table
        return table

    def update_table(self, table: TableMeta) -> TableMeta:
        key = (table.project_id, table.dataset_id, table.table_id)
        if key not in self._tables:
            raise resource_not_found(
                ResourceRef("table", table.project_id, table.dataset_id, table.table_id),
            )
        self._tables[key] = table
        return table

    def delete_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        key = (project_id, dataset_id, table_id)
        if key not in self._tables:
            if not_found_ok:
                return
            raise resource_not_found(
                ResourceRef("table", project_id, dataset_id, table_id),
            )
        del self._tables[key]

    def list_views(self, project_id: str, dataset_id: str) -> tuple[TableMeta, ...]:
        return tuple(t for t in self.list_tables(project_id, dataset_id) if t.table_type == "VIEW")

    def list_partitions(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[PartitionMeta, ...]:
        """Discover partitions by querying DuckDB.

        When no engine is wired (the unit-test default) returns an
        empty tuple — the in-memory catalog has no rows to inspect.
        The full enumeration logic lives in
        :func:`bqemulator.storage.partition_state.list_partitions_for_table`
        which is the single source of truth shared with the
        DuckDB-backed repository.
        """
        if self._engine is None:
            return ()
        table = self.get_table(project_id, dataset_id, table_id)
        if table is None:
            return ()
        from bqemulator.storage.partition_state import (
            list_partitions_for_table,
        )

        return list_partitions_for_table(self._engine, table)

    # -- Routines ---------------------------------------------------------

    def list_routines(
        self,
        project_id: str,
        dataset_id: str,
    ) -> tuple[RoutineMeta, ...]:
        return tuple(
            r for (p, d, _r), r in self._routines.items() if p == project_id and d == dataset_id
        )

    def get_routine(
        self,
        project_id: str,
        dataset_id: str,
        routine_id: str,
    ) -> RoutineMeta | None:
        return self._routines.get((project_id, dataset_id, routine_id))

    def create_routine(self, routine: RoutineMeta) -> RoutineMeta:
        key = (routine.project_id, routine.dataset_id, routine.routine_id)
        if key in self._routines:
            raise resource_already_exists(
                ResourceRef("routine", *key),
            )
        if (routine.project_id, routine.dataset_id) not in self._datasets:
            raise resource_not_found(
                ResourceRef("dataset", routine.project_id, routine.dataset_id),
            )
        self._routines[key] = routine
        return routine

    def update_routine(self, routine: RoutineMeta) -> RoutineMeta:
        key = (routine.project_id, routine.dataset_id, routine.routine_id)
        if key not in self._routines:
            raise resource_not_found(ResourceRef("routine", *key))
        self._routines[key] = routine
        return routine

    def delete_routine(
        self,
        project_id: str,
        dataset_id: str,
        routine_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        key = (project_id, dataset_id, routine_id)
        if key not in self._routines:
            if not_found_ok:
                return
            raise resource_not_found(ResourceRef("routine", project_id, dataset_id, routine_id))
        del self._routines[key]

    # -- Models -----------------------------------------------------------

    def list_models(
        self,
        project_id: str,
        dataset_id: str,
    ) -> tuple[ModelMeta, ...]:
        return tuple(
            m for (p, d, _m), m in self._models.items() if p == project_id and d == dataset_id
        )

    def get_model(
        self,
        project_id: str,
        dataset_id: str,
        model_id: str,
    ) -> ModelMeta | None:
        return self._models.get((project_id, dataset_id, model_id))

    def create_model(self, model: ModelMeta) -> ModelMeta:
        key = (model.project_id, model.dataset_id, model.model_id)
        if key in self._models:
            raise resource_already_exists(ResourceRef("model", *key))
        if (model.project_id, model.dataset_id) not in self._datasets:
            raise resource_not_found(
                ResourceRef("dataset", model.project_id, model.dataset_id),
            )
        self._models[key] = model
        return model

    def update_model(self, model: ModelMeta) -> ModelMeta:
        key = (model.project_id, model.dataset_id, model.model_id)
        if key not in self._models:
            raise resource_not_found(ResourceRef("model", *key))
        self._models[key] = model
        return model

    def delete_model(
        self,
        project_id: str,
        dataset_id: str,
        model_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        key = (project_id, dataset_id, model_id)
        if key not in self._models:
            if not_found_ok:
                return
            raise resource_not_found(ResourceRef("model", project_id, dataset_id, model_id))
        del self._models[key]

    # -- Jobs -------------------------------------------------------------

    def list_jobs(
        self,
        project_id: str,
        *,
        state_filter: str | None = None,
        max_results: int = 100,
    ) -> tuple[JobMeta, ...]:
        rows = [
            j
            for (p, _j), j in self._jobs.items()
            if p == project_id and (state_filter is None or j.state == state_filter)
        ]
        rows.sort(key=lambda j: j.creation_time, reverse=True)
        return tuple(rows[:max_results])

    def get_job(self, project_id: str, job_id: str) -> JobMeta | None:
        return self._jobs.get((project_id, job_id))

    def upsert_job(self, job: JobMeta) -> JobMeta:
        self._jobs[(job.project_id, job.job_id)] = job
        return job

    def delete_job(
        self,
        project_id: str,
        job_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        key = (project_id, job_id)
        if key not in self._jobs:
            if not_found_ok:
                return
            raise resource_not_found(ResourceRef("job", project_id, resource_id=job_id))
        del self._jobs[key]

    # -- Snapshots -------------------------------------------------------

    def list_snapshots_for_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[SnapshotMeta, ...]:
        matches = [
            s
            for s in self._snapshots.values()
            if s.project_id == project_id and s.dataset_id == dataset_id and s.table_id == table_id
        ]
        matches.sort(key=lambda s: s.snapshot_time)
        return tuple(matches)

    def list_all_snapshots(self) -> tuple[SnapshotMeta, ...]:
        return tuple(sorted(self._snapshots.values(), key=lambda s: s.snapshot_time))

    def create_snapshot(self, snapshot: SnapshotMeta) -> SnapshotMeta:
        if snapshot.snapshot_id in self._snapshots:
            raise resource_already_exists(
                ResourceRef(
                    "snapshot",
                    snapshot.project_id,
                    snapshot.dataset_id,
                    snapshot.snapshot_id,
                ),
            )
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def delete_snapshot(
        self,
        snapshot_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        if snapshot_id not in self._snapshots:
            if not_found_ok:
                return
            raise resource_not_found(
                ResourceRef("snapshot", "", resource_id=snapshot_id),
            )
        del self._snapshots[snapshot_id]

    # -- Materialized views ---------------------------------------------

    def list_materialized_views(
        self,
        project_id: str,
        dataset_id: str,
    ) -> tuple[MaterializedViewMeta, ...]:
        return tuple(
            v for (p, d, _t), v in self._mviews.items() if p == project_id and d == dataset_id
        )

    def list_all_materialized_views(self) -> tuple[MaterializedViewMeta, ...]:
        return tuple(self._mviews.values())

    def get_materialized_view(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> MaterializedViewMeta | None:
        return self._mviews.get((project_id, dataset_id, table_id))

    def upsert_materialized_view(
        self,
        view: MaterializedViewMeta,
    ) -> MaterializedViewMeta:
        key = (view.project_id, view.dataset_id, view.table_id)
        self._mviews[key] = view
        return view

    def delete_materialized_view(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        key = (project_id, dataset_id, table_id)
        if key not in self._mviews:
            if not_found_ok:
                return
            raise resource_not_found(
                ResourceRef("materialized_view", project_id, dataset_id, table_id),
            )
        del self._mviews[key]

    # -- Row access policies --------------------------------------------

    def list_row_access_policies(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[RowAccessPolicyMeta, ...]:
        return tuple(
            p
            for (p_id, d_id, t_id, _pid), p in self._row_access.items()
            if p_id == project_id and d_id == dataset_id and t_id == table_id
        )

    def list_all_row_access_policies(self) -> tuple[RowAccessPolicyMeta, ...]:
        return tuple(self._row_access.values())

    def get_row_access_policy(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
    ) -> RowAccessPolicyMeta | None:
        return self._row_access.get(
            (project_id, dataset_id, table_id, policy_id),
        )

    def create_row_access_policy(
        self,
        policy: RowAccessPolicyMeta,
    ) -> RowAccessPolicyMeta:
        key = (
            policy.project_id,
            policy.dataset_id,
            policy.table_id,
            policy.policy_id,
        )
        if key in self._row_access:
            raise resource_already_exists(_rap_ref(*key))
        self._row_access[key] = policy
        return policy

    def update_row_access_policy(
        self,
        policy: RowAccessPolicyMeta,
    ) -> RowAccessPolicyMeta:
        key = (
            policy.project_id,
            policy.dataset_id,
            policy.table_id,
            policy.policy_id,
        )
        if key not in self._row_access:
            raise resource_not_found(_rap_ref(*key))
        self._row_access[key] = policy
        return policy

    def delete_row_access_policy(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
        *,
        not_found_ok: bool = False,
    ) -> None:
        key = (project_id, dataset_id, table_id, policy_id)
        if key not in self._row_access:
            if not_found_ok:
                return
            raise resource_not_found(_rap_ref(*key))
        del self._row_access[key]


def _rap_ref(
    project_id: str,
    dataset_id: str,
    table_id: str,
    policy_id: str,
) -> ResourceRef:
    """Build a ResourceRef for a row access policy with table+policy folded in."""
    return ResourceRef(
        "row_access_policy",
        project_id,
        dataset_id,
        resource_id=f"{table_id}.{policy_id}",
    )


__all__ = ["MemoryCatalogRepository"]
