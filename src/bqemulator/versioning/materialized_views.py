"""Materialized-view registry, refresh, and staleness tracking.

Implements the Phase 7 MV model locked in by ADR 0017 — event-driven
staleness flagging plus lazy recompute on read.

Lifecycle:

1. ``CREATE MATERIALIZED VIEW mv AS <query>``
   → parse ``<query>`` with SQLGlot, extract base-table refs, run the
   query through the regular SQL translation pipeline, materialise
   the rows into a regular dataset table, store ``TableMeta`` +
   ``MaterializedViewMeta`` + dependency entries in the catalog,
   subscribe the MV to ``TableDataChanged`` events on each base
   table.
2. ``TableDataChanged(base)``
   → every subscribed MV flips ``is_stale=True`` in the catalog.
3. ``SELECT ... FROM mv``
   → the jobs executor consults
   :meth:`MaterializedViewManager.refresh_if_stale` before executing;
   a stale MV is recomputed under the write lock.
4. ``REFRESH MATERIALIZED VIEW mv``
   → :meth:`MaterializedViewManager.refresh` forces a recompute
   regardless of the flag.
5. ``DROP MATERIALIZED VIEW mv``
   → drop the physical table, clear subscriptions, remove catalog
   entries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import (
    MaterializedViewMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
)
from bqemulator.domain.errors import (
    InvalidQueryError,
    ResourceRef,
    resource_already_exists,
    resource_not_found,
)
from bqemulator.domain.events import TableDataChanged
from bqemulator.observability.logging_ import get_logger
from bqemulator.sql.table_rewriter import rewrite_table_refs
from bqemulator.sql.translator import SQLTranslator
from bqemulator.storage.sql_identifiers import quoted_table_ref

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from bqemulator.api.dependencies import AppContext
    from bqemulator.domain.events import DomainEvent


_log = get_logger(__name__)


class MaterializedViewManager:
    """Coordinates MV lifecycle + refresh."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._translator = SQLTranslator()

    # -- Public API ------------------------------------------------------

    async def create(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        view_query: str,
    ) -> MaterializedViewMeta:
        """Materialise a new MV and register its dependencies."""
        if self._ctx.catalog.get_table(project_id, dataset_id, table_id) is not None:
            raise resource_already_exists(
                ResourceRef("table", project_id, dataset_id, table_id),
            )
        if self._ctx.catalog.get_dataset(project_id, dataset_id) is None:
            raise resource_not_found(
                ResourceRef("dataset", project_id, dataset_id),
            )

        base_tables = extract_base_tables(view_query, project_id)
        if not base_tables:
            raise InvalidQueryError(
                "CREATE MATERIALIZED VIEW requires at least one base table",
            )
        # Every base table must exist or the MV cannot refresh.
        for bp, bd, bt in base_tables:
            if self._ctx.catalog.get_table(bp, bd, bt) is None:
                raise resource_not_found(ResourceRef("table", bp, bd, bt))

        now = self._ctx.clock.now()
        target_ref = quoted_table_ref(project_id, dataset_id, table_id)
        duckdb_sql = self._translate(view_query, project_id)

        async with self._ctx.engine.write_lock():
            self._ctx.engine.execute(
                f"CREATE OR REPLACE TABLE {target_ref} AS {duckdb_sql}",
            )
            schema = self._read_arrow_schema(project_id, dataset_id, table_id)
            count_row = self._ctx.engine.execute(
                f"SELECT COUNT(*) FROM {target_ref}",
            ).fetchone()
            num_rows = int(count_row[0]) if count_row else 0

            table_meta = TableMeta(
                project_id=project_id,
                dataset_id=dataset_id,
                table_id=table_id,
                table_type="MATERIALIZED_VIEW",
                schema=schema,
                creation_time=now,
                last_modified_time=now,
                num_rows=num_rows,
                num_bytes=0,
                etag=generate_etag(
                    project_id,
                    dataset_id,
                    table_id,
                    "MATERIALIZED_VIEW",
                    str(now),
                ),
                view_query=view_query,
            )
            self._ctx.catalog.create_table(table_meta)

            mv_meta = MaterializedViewMeta(
                project_id=project_id,
                dataset_id=dataset_id,
                table_id=table_id,
                view_query=view_query,
                base_tables=tuple(base_tables),
                last_refresh_time=now,
                is_stale=False,
            )
            self._ctx.catalog.upsert_materialized_view(mv_meta)

        _subscribe_mv(self._ctx, mv_meta)
        return mv_meta

    async def drop(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> None:
        """Drop an MV and clear its subscriptions."""
        existing_table = self._ctx.catalog.get_table(project_id, dataset_id, table_id)
        if existing_table is None or existing_table.table_type != "MATERIALIZED_VIEW":
            raise resource_not_found(
                ResourceRef("materialized_view", project_id, dataset_id, table_id),
            )
        target_ref = quoted_table_ref(project_id, dataset_id, table_id)
        async with self._ctx.engine.write_lock():
            self._ctx.engine.execute(f"DROP TABLE IF EXISTS {target_ref}")
            self._ctx.catalog.delete_table(
                project_id,
                dataset_id,
                table_id,
                not_found_ok=True,
            )
            self._ctx.catalog.delete_materialized_view(
                project_id,
                dataset_id,
                table_id,
                not_found_ok=True,
            )
        _unsubscribe_mv(self._ctx, project_id, dataset_id, table_id)

    async def refresh(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> MaterializedViewMeta:
        """Force-refresh an MV regardless of staleness."""
        mv = self._ctx.catalog.get_materialized_view(project_id, dataset_id, table_id)
        if mv is None:
            raise resource_not_found(
                ResourceRef("materialized_view", project_id, dataset_id, table_id),
            )
        return await self._do_refresh(mv, force=True)

    async def refresh_if_stale(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> MaterializedViewMeta | None:
        """Refresh the MV only when ``is_stale=True``.

        Returns the updated :class:`MaterializedViewMeta` (or the current
        one if no refresh was needed). Returns ``None`` if the table is
        not an MV.

        The staleness flag is re-checked inside :meth:`_do_refresh`'s
        write lock to collapse concurrent stale-readers onto a single
        recompute — the second reader sees ``is_stale=False`` and
        skips the redundant CTAS.
        """
        mv = self._ctx.catalog.get_materialized_view(project_id, dataset_id, table_id)
        if mv is None:
            return None
        if not mv.is_stale:
            return mv
        return await self._do_refresh(mv)

    # -- Internals -------------------------------------------------------

    async def _do_refresh(
        self,
        mv: MaterializedViewMeta,
        *,
        force: bool = False,
    ) -> MaterializedViewMeta:
        duckdb_sql = self._translate(mv.view_query, mv.project_id)
        target_ref = quoted_table_ref(mv.project_id, mv.dataset_id, mv.table_id)
        now = self._ctx.clock.now()

        async with self._ctx.engine.write_lock():
            # Re-check inside the lock so concurrent stale-readers
            # collapse onto a single recompute. ``force=True`` bypasses
            # the recheck for explicit ``REFRESH MATERIALIZED VIEW``.
            if not force:
                fresh = self._ctx.catalog.get_materialized_view(
                    mv.project_id,
                    mv.dataset_id,
                    mv.table_id,
                )
                if fresh is None:
                    return mv
                if not fresh.is_stale:
                    return fresh
                mv = fresh
            self._ctx.engine.execute(
                f"CREATE OR REPLACE TABLE {target_ref} AS {duckdb_sql}",
            )
            count_row = self._ctx.engine.execute(
                f"SELECT COUNT(*) FROM {target_ref}",
            ).fetchone()
            num_rows = int(count_row[0]) if count_row else 0

            table_meta = self._ctx.catalog.get_table(
                mv.project_id,
                mv.dataset_id,
                mv.table_id,
            )
            if table_meta is not None:
                self._ctx.catalog.update_table(
                    table_meta.model_copy(
                        update={
                            "num_rows": num_rows,
                            "last_modified_time": now,
                        },
                    ),
                )
            refreshed = mv.model_copy(
                update={"last_refresh_time": now, "is_stale": False},
            )
            self._ctx.catalog.upsert_materialized_view(refreshed)

        return refreshed

    def _translate(self, bq_sql: str, project_id: str) -> str:
        """Translate + table-rewrite the MV source query."""
        from bqemulator.domain.result import Err, Ok

        match self._translator.translate(bq_sql):
            case Ok(duckdb_sql):
                pass
            case Err(error):
                raise error
        return rewrite_table_refs(duckdb_sql, project_id)

    def _read_arrow_schema(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> TableSchema:
        """Inspect the freshly-materialised table to build a TableSchema."""
        from bqemulator.storage.arrow_bridge import (
            arrow_type_to_bq_type_name,
            introspect_arrow_schema,
        )

        target_ref = quoted_table_ref(project_id, dataset_id, table_id)
        schema = introspect_arrow_schema(self._ctx.engine, target_ref)
        fields = tuple(
            TableFieldSchema(
                name=schema.field(i).name,
                type=arrow_type_to_bq_type_name(schema.field(i).type),
                mode="NULLABLE",
            )
            for i in range(len(schema))
        )
        return TableSchema(fields=fields)


# ---------------------------------------------------------------------------
# Dependency extraction
# ---------------------------------------------------------------------------


def extract_base_tables(
    view_query: str,
    project_id: str,
) -> list[tuple[str, str, str]]:
    """Walk ``view_query`` and return every base-table reference.

    Returns a list of ``(project, dataset, table)`` triples, deduplicated
    while preserving iteration order. Raises :class:`InvalidQueryError`
    if the query cannot be parsed.
    """
    try:
        tree = sqlglot.parse_one(view_query, read="bigquery")
    except Exception as exc:
        raise InvalidQueryError(
            f"Cannot parse materialized-view query: {exc}",
        ) from exc

    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for table_node in tree.find_all(exp.Table):
        if isinstance(table_node.this, exp.Anonymous):
            # TVF call — not a base table.
            continue
        table_name = table_node.name
        dataset_name = table_node.db
        project_name = table_node.catalog or project_id
        if not table_name or not dataset_name:
            continue
        key = (project_name, dataset_name, table_name)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# ---------------------------------------------------------------------------
# Event-bus subscription wiring
# ---------------------------------------------------------------------------


def _subscribe_mv(ctx: AppContext, mv: MaterializedViewMeta) -> None:
    """Register a ``TableDataChanged`` handler that marks ``mv`` stale."""
    handler = _make_stale_handler(ctx, mv)
    _register_handler(ctx, mv, handler)
    ctx.events.subscribe(TableDataChanged, handler)


def _unsubscribe_mv(
    ctx: AppContext,
    project_id: str,
    dataset_id: str,
    table_id: str,
) -> None:
    """Remove the ``TableDataChanged`` handler for a dropped MV."""
    handlers = _handler_registry(ctx)
    key = (project_id, dataset_id, table_id)
    handler = handlers.pop(key, None)
    if handler is not None:
        ctx.events.unsubscribe(TableDataChanged, handler)


def _make_stale_handler(
    ctx: AppContext,
    mv: MaterializedViewMeta,
) -> Callable[[DomainEvent], None]:
    """Return a handler that flips ``is_stale=True`` on base-table changes."""
    base_set = {tuple(b) for b in mv.base_tables}
    key = (mv.project_id, mv.dataset_id, mv.table_id)

    def _handler(event: DomainEvent) -> None:
        if not isinstance(event, TableDataChanged):
            return
        if (event.project_id, event.dataset_id, event.table_id) not in base_set:
            return
        fresh = ctx.catalog.get_materialized_view(*key)
        if fresh is None or fresh.is_stale:
            return
        ctx.catalog.upsert_materialized_view(
            fresh.model_copy(update={"is_stale": True}),
        )

    return _handler


_MV_HANDLER_REGISTRY: dict[
    int,
    dict[tuple[str, str, str], Callable[[DomainEvent], None]],
] = {}


def _handler_registry(ctx: AppContext) -> dict[tuple[str, str, str], Callable[[DomainEvent], None]]:
    """Return (creating if needed) the per-context MV handler registry.

    Keyed by ``id(ctx)`` so each ``EmulatorServer`` instance has its own
    registry without mutating the frozen :class:`AppContext`. The entry
    is cleared via :func:`clear_subscriptions_for_context` when the
    server stops.
    """
    key = id(ctx)
    registry = _MV_HANDLER_REGISTRY.get(key)
    if registry is None:
        registry = {}
        _MV_HANDLER_REGISTRY[key] = registry
    return registry


def clear_subscriptions_for_context(ctx: AppContext) -> None:
    """Remove every MV subscription attached to ``ctx``.

    Called by the composition root during shutdown so stopped servers
    don't keep dead handlers alive.
    """
    registry = _MV_HANDLER_REGISTRY.pop(id(ctx), None)
    if registry is None:
        return
    for handler in registry.values():
        ctx.events.unsubscribe(TableDataChanged, handler)


def _register_handler(
    ctx: AppContext,
    mv: MaterializedViewMeta,
    handler: Callable[[DomainEvent], None],
) -> None:
    reg = _handler_registry(ctx)
    reg[(mv.project_id, mv.dataset_id, mv.table_id)] = handler


def hydrate_subscriptions(ctx: AppContext) -> None:
    """Rebuild MV event subscriptions from the catalog.

    Called by the composition root on startup so restarts don't lose
    staleness tracking for persisted MVs.
    """
    for mv in ctx.catalog.list_all_materialized_views():
        _subscribe_mv(ctx, mv)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


__all__ = [
    "MaterializedViewManager",
    "clear_subscriptions_for_context",
    "extract_base_tables",
    "hydrate_subscriptions",
]
