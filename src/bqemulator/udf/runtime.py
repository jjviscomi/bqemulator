"""UDF runtime protocol + registry.

Each ``UDFRuntime`` implementation knows how to materialize a
``RoutineMeta`` instance into the DuckDB engine and how to deregister
it on delete.

The :class:`UDFRegistry` dispatches on ``(routine_type, language)`` and
is the single entry point for the REST layer, the job executor, and the
scripting interpreter.

On server startup, every routine already present in the catalog is
re-materialized to keep persistent-mode restarts idempotent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.observability.logging_ import get_logger

if TYPE_CHECKING:
    from bqemulator.catalog.models import RoutineMeta
    from bqemulator.catalog.repository import CatalogRepository
    from bqemulator.config import Settings
    from bqemulator.storage.engine import DuckDBEngine

_log = get_logger(__name__)


@runtime_checkable
class UDFRuntime(Protocol):
    """Strategy protocol for a routine runtime."""

    def materialize(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Register ``routine`` with DuckDB, replacing any prior version."""
        ...

    def deregister(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Unregister ``routine`` from DuckDB. Idempotent."""
        ...


class UDFRegistry:
    """Owns the per-runtime strategies and hydrates on startup.

    Thread-safety: DuckDB's single-writer model and the registry's own
    lookups are dict reads (atomic in CPython). Writes serialise on the
    engine's write lock at the call site.
    """

    def __init__(self, settings: Settings) -> None:
        # Import lazily so modules can depend on this module without
        # importing mini-racer at top level.
        from bqemulator.udf.js_udf import JavaScriptUDFRuntime
        from bqemulator.udf.sql_udf import SQLUDFRuntime
        from bqemulator.udf.table_valued import TableValuedRuntime

        self._settings = settings
        self._sql = SQLUDFRuntime()
        self._tvf = TableValuedRuntime()
        self._js = JavaScriptUDFRuntime(
            cpu_timeout_ms=settings.udf_js_timeout_ms,
            memory_limit_bytes=settings.udf_js_memory_bytes,
        )
        self._routines: dict[tuple[str, str, str], RoutineMeta] = {}

    def _dispatch(self, routine: RoutineMeta) -> UDFRuntime:
        """Return the runtime for a given routine."""
        if routine.routine_type == "TABLE_VALUED_FUNCTION":
            return self._tvf
        if routine.routine_type == "PROCEDURE":
            # Procedures do not register anything in DuckDB — their body
            # is interpreted by the scripting interpreter. We return a
            # no-op runtime so create/update/delete behave uniformly.
            return _NoopRuntime()
        if routine.language == "JAVASCRIPT":
            return self._js
        if routine.language == "SQL":
            return self._sql
        raise InvalidQueryError(
            f"Unsupported routine combination: {routine.routine_type} / {routine.language}",
        )

    def materialize(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Register the routine and remember it for re-materialization."""
        runtime = self._dispatch(routine)
        runtime.materialize(routine, engine)
        key = (routine.project_id, routine.dataset_id, routine.routine_id)
        self._routines[key] = routine
        _log.debug(
            "udf.materialize",
            routine=routine.routine_id,
            type=routine.routine_type,
            language=routine.language,
        )

    def deregister(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Remove the routine from DuckDB and registry bookkeeping."""
        runtime = self._dispatch(routine)
        runtime.deregister(routine, engine)
        key = (routine.project_id, routine.dataset_id, routine.routine_id)
        self._routines.pop(key, None)
        _log.debug("udf.deregister", routine=routine.routine_id)

    def hydrate(self, catalog: CatalogRepository, engine: DuckDBEngine) -> None:
        """Re-materialize every routine currently in the catalog.

        Called by the composition root after both engine and catalog are
        ready. Errors during hydration are logged but do not fail startup
        — a broken routine should not block the whole server.
        """
        datasets: set[tuple[str, str]] = set()
        # The catalog API is scoped per-(project, dataset) so we need to
        # enumerate datasets first; in-memory and DuckDB backends both
        # expose a flat routine table internally, but through the public
        # protocol we iterate.
        for ds in _iter_all_datasets(catalog):
            datasets.add(ds)
        for project_id, dataset_id in datasets:
            for routine in catalog.list_routines(project_id, dataset_id):
                try:
                    self.materialize(routine, engine)
                except Exception as exc:  # noqa: BLE001 — hydration must be best-effort
                    _log.warning(
                        "udf.hydrate.failed",
                        routine=routine.routine_id,
                        error=str(exc),
                    )


class _NoopRuntime:
    """No-op runtime used for routine types without a DuckDB counterpart.

    Procedures fall into this bucket — their bodies execute through the
    scripting interpreter on CALL, so there is nothing to materialize.
    """

    def materialize(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:  # noqa: ARG002
        """Do nothing — procedure bodies execute at CALL time."""
        return

    def deregister(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:  # noqa: ARG002
        """Do nothing — procedure bodies execute at CALL time."""
        return


def _iter_all_datasets(catalog: CatalogRepository) -> list[tuple[str, str]]:
    """Enumerate every (project, dataset) pair in the catalog.

    The catalog protocol is scoped per-project for listing, so we walk
    projects first via the memory-repo internals when available, falling
    through to an empty list otherwise. This helper is best-effort and
    only runs during hydration.
    """
    # Inspect the backing store if the repository exposes it — both
    # backends do via ``_datasets`` / ``_cache._datasets``.
    inner = getattr(catalog, "_datasets", None)
    if inner is None:
        cache = getattr(catalog, "_cache", None)
        inner = getattr(cache, "_datasets", None)
    if inner is None:
        return []
    return [(p, d) for (p, d) in inner]


__all__ = ["UDFRegistry", "UDFRuntime"]
