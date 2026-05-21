"""Script-local TEMP-function registry.

BigQuery scripting lets a script create a function with a single-part
identifier (``CREATE TEMP FUNCTION foo(...)``) and then call it via the
same single-part identifier (``SELECT foo(...)``). The function lives
only for the script's lifetime — it is never visible to other scripts
and does not appear in the routines catalogue.

The emulator's regular routine machinery requires a fully-qualified
``project.dataset.routine`` reference (see
[ADR 0014](../../docs/adr/0014-udf-materialization-strategy.md)); this
registry adds the local-scope lookup pass [ADR 0023 §1.D] mandates:

1. ``CREATE TEMP FUNCTION foo(...)`` registers ``foo`` under a
   synthetic dataset id that is unique to *this* registry instance —
   ``_bqemu_temp_<hex>`` — so concurrent scripts on the same engine
   never collide.
2. The routine is materialised via the regular
   :class:`~bqemulator.udf.runtime.UDFRegistry`, so the body translates
   through :class:`~bqemulator.sql.translator.SQLTranslator` and lands
   as a DuckDB ``MACRO`` under the flat
   ``<project>__<synthetic-dataset>__<routine>`` name.
3. ``SELECT foo(args)`` is preprocessed by
   :meth:`TempRoutineRegistry.rewrite_calls` — every BigQuery AST
   :class:`~sqlglot.exp.Anonymous` node whose function name is in the
   registry is renamed to the materialised flat name *before* the rest
   of the SQL pipeline runs. The downstream translator therefore sees
   a regular qualified call.
4. :meth:`TempRoutineRegistry.cleanup` is the script interpreter's
   responsibility — call it in a ``finally`` arm so the materialised
   DuckDB macros do not leak across script invocations, preserving the
   ADR 0014 scope guarantee.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import uuid

import sqlglot
from sqlglot import exp

from bqemulator.observability.logging_ import get_logger
from bqemulator.udf.naming import qualified_routine_name

if TYPE_CHECKING:
    from bqemulator.catalog.models import RoutineMeta
    from bqemulator.storage.engine import DuckDBEngine
    from bqemulator.udf.runtime import UDFRegistry

_log = get_logger(__name__)


class TempRoutineRegistry:
    """Per-script-interpreter registry of script-local TEMP routines.

    Each :class:`~bqemulator.scripting.interpreter.ScriptInterpreter`
    owns one instance for the duration of a script's execution. The
    instance owns a synthetic ``_bqemu_temp_<hex>`` dataset id so the
    flat MACRO names every TEMP routine materialises under are unique
    across concurrent scripts on the same engine.
    """

    def __init__(
        self,
        *,
        engine: DuckDBEngine,
        udf_registry: UDFRegistry,
    ) -> None:
        self._engine = engine
        self._udf_registry = udf_registry
        self._synthetic_dataset = f"_bqemu_temp_{uuid.uuid4().hex}"
        self._routines: dict[str, RoutineMeta] = {}

    @property
    def synthetic_dataset(self) -> str:
        """Synthetic dataset id used for the lifetime of this registry."""
        return self._synthetic_dataset

    def register(self, bare_name: str, routine: RoutineMeta) -> None:
        """Materialise ``routine`` under ``bare_name`` and remember it.

        ``routine.dataset_id`` must equal :attr:`synthetic_dataset` so
        the materialised flat name is unique to this registry. The
        registry takes ownership of the routine for the rest of its
        lifetime — :meth:`cleanup` deregisters it.
        """
        from bqemulator.domain.errors import InvalidQueryError

        if routine.dataset_id != self._synthetic_dataset:
            raise InvalidQueryError(
                "TempRoutineRegistry routine must use the registry's synthetic dataset id",
            )
        self._udf_registry.materialize(routine, self._engine)
        self._routines[bare_name] = routine
        _log.debug(
            "temp_routine.register",
            name=bare_name,
            qualified=qualified_routine_name(routine),
        )

    def resolve(self, bare_name: str) -> RoutineMeta | None:
        """Return the RoutineMeta registered under ``bare_name``, or None."""
        return self._routines.get(bare_name)

    def rewrite_calls(self, bq_sql: str) -> str:
        """Rewrite bare TEMP-function calls to their qualified DuckDB names.

        Walks the BigQuery AST and replaces every ``Anonymous`` node
        whose function name is in the registry with the flat name the
        SQL UDF runtime materialised the routine under. The downstream
        translator and DuckDB then see a regular qualified call.

        Inputs the SQLGlot BigQuery parser cannot consume are returned
        unchanged — the downstream pipeline produces a clean diagnostic.
        """
        if not self._routines:
            return bq_sql
        try:
            tree = sqlglot.parse_one(bq_sql, read="bigquery")
        except Exception:  # noqa: BLE001 — best-effort rewrite
            return bq_sql
        modified = False
        for anon in list(tree.find_all(exp.Anonymous)):
            routine = self._routines.get(anon.name)
            if routine is None:
                continue
            anon.set("this", qualified_routine_name(routine))
            modified = True
        if not modified:
            return bq_sql
        return tree.sql(dialect="bigquery")

    def cleanup(self) -> None:
        """Deregister every materialised macro. Idempotent."""
        for routine in list(self._routines.values()):
            try:
                self._udf_registry.deregister(routine, self._engine)
            except Exception as exc:  # noqa: BLE001 — cleanup must not raise
                _log.warning(
                    "temp_routine.cleanup.failed",
                    routine=routine.routine_id,
                    error=str(exc),
                )
        self._routines.clear()


__all__ = ["TempRoutineRegistry"]
