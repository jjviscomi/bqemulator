"""``statementType`` / ``ddlOperationPerformed`` and execution for single routine DDL.

Real BigQuery's ``jobs.query`` contract for a single routine-DDL
statement, pinned by the ``routines_scripting/routine_ddl_*``
conformance corpus:

* ``CREATE FUNCTION`` (scalar SQL or JS) → ``statementType``
  ``CREATE_FUNCTION``, ``ddlOperationPerformed`` ``CREATE`` /
  ``REPLACE``.
* ``CREATE TABLE FUNCTION`` (TVF) → ``CREATE_TABLE_FUNCTION`` / same.
* ``CREATE PROCEDURE`` → ``statementType`` **``SCRIPT``** with no
  ``ddlOperationPerformed`` — BigQuery classifies procedure definition
  as a script, not a DDL. The emulator already reports ``SCRIPT`` for
  scripted input, so this module deliberately does **not** reclassify
  ``CreateProcedureStmt``.
* ``DROP FUNCTION`` / ``DROP PROCEDURE`` / ``DROP TABLE FUNCTION`` →
  ``DROP_FUNCTION`` / ``DROP_PROCEDURE`` / ``DROP_TABLE_FUNCTION``,
  ``ddlOperationPerformed`` ``DROP`` (or ``SKIP`` when
  ``IF EXISTS`` finds nothing).

The CREATE forms run through the scripting interpreter (which registers
the routine); this module only refines the reported ``statementType`` /
operation. The DROP forms have no DuckDB counterpart — a procedure
isn't a DuckDB object, and a UDF macro drop must mirror the registry
bookkeeping — so they are executed here against the catalog + UDF
registry rather than being handed to DuckDB (which raises
``syntax error at "PROCEDURE"`` / ``Macro … does not exist``).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any

import sqlglot
from sqlglot import exp

from bqemulator.scripting.ast import CreateFunctionStmt

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext

_REF_FULLY_QUALIFIED = 3
_REF_DATASET_QUALIFIED = 2

#: ``DROP TABLE FUNCTION`` is not in sqlglot's BigQuery grammar (it
#: raises a ParseError), so it is detected via this regex instead.
_DROP_TABLE_FUNCTION_RE = re.compile(
    r"^\s*DROP\s+TABLE\s+FUNCTION\s+(?:(?P<exists>IF\s+EXISTS)\s+)?(?P<name>.+?)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)

#: SQLGlot ``Drop`` ``kind`` → BigQuery ``statementType`` for routine drops.
_DROP_KIND_TO_STATEMENT_TYPE = {
    "FUNCTION": "DROP_FUNCTION",
    "PROCEDURE": "DROP_PROCEDURE",
}


def classify_create_routine(script: Any) -> str:
    """Return the ``statementType`` for a single CREATE-routine script.

    ``CREATE_FUNCTION`` for a scalar SQL / JS UDF, ``CREATE_TABLE_FUNCTION``
    for a TVF, and ``""`` for anything else — including
    ``CreateProcedureStmt``, which BigQuery reports as ``SCRIPT`` (the
    caller's default for scripted input) rather than a DDL type.
    """
    stmt = _single_statement(script)
    if isinstance(stmt, CreateFunctionStmt):
        if stmt.routine_type == "TABLE_VALUED_FUNCTION":
            return "CREATE_TABLE_FUNCTION"
        return "CREATE_FUNCTION"
    return ""


def resolve_create_routine_operation(script: Any, project_id: str, ctx: AppContext) -> str:
    """Resolve ``ddlOperationPerformed`` for a single CREATE FUNCTION / TVF.

    Must run **before** the interpreter registers the routine: ``REPLACE``
    is reported only when ``OR REPLACE`` targets a routine that already
    exists in the catalog; otherwise ``CREATE``. Returns ``""`` when the
    script is not a single CREATE FUNCTION / TVF (e.g. a procedure, whose
    operation field is absent).
    """
    stmt = _single_statement(script)
    if not isinstance(stmt, CreateFunctionStmt):
        return ""
    ref = _resolve_routine_ref(stmt.name, project_id)
    if ref is not None and stmt.or_replace and ctx.catalog.get_routine(*ref) is not None:
        return "REPLACE"
    return "CREATE"


@dataclass(frozen=True)
class DropRoutineRef:
    """A resolved single ``DROP {FUNCTION|PROCEDURE|TABLE FUNCTION}`` target."""

    statement_type: str
    project_id: str
    dataset_id: str
    routine_id: str
    if_exists: bool


def detect_drop_routine(bq_sql: str, default_project: str) -> DropRoutineRef | None:
    """Return a :class:`DropRoutineRef` for a single routine drop, else ``None``.

    ``DROP TABLE FUNCTION`` is matched by regex (sqlglot cannot parse
    it); ``DROP FUNCTION`` / ``DROP PROCEDURE`` are matched via the
    sqlglot AST. Returns ``None`` for every non-routine DROP (table,
    view, schema, …) so the caller falls through to the normal path.
    """
    table_fn = _DROP_TABLE_FUNCTION_RE.match(bq_sql)
    if table_fn is not None:
        ref = _ref_from_name(table_fn.group("name"), default_project)
        if ref is None:
            return None
        return DropRoutineRef("DROP_TABLE_FUNCTION", *ref, bool(table_fn.group("exists")))

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — not a routine drop / unparseable
        return None
    if not isinstance(tree, exp.Drop):
        return None
    statement_type = _DROP_KIND_TO_STATEMENT_TYPE.get((tree.args.get("kind") or "").upper())
    if statement_type is None:
        return None
    target = tree.this
    if not isinstance(target, exp.Table):
        return None
    ref = _split_routine_table(target, default_project)
    if ref is None:
        return None
    return DropRoutineRef(statement_type, *ref, bool(tree.args.get("exists")))


async def run_drop_routine(ref: DropRoutineRef, ctx: AppContext) -> str:
    """Execute a routine drop; return ``ddlOperationPerformed`` (DROP / SKIP).

    Mirrors the CREATE-routine registration: deregister the routine from
    the UDF registry (removing any DuckDB macro for a SQL UDF / TVF) and
    delete its catalog entry. A missing target with ``IF EXISTS`` is a
    no-op reporting ``SKIP``; without ``IF EXISTS`` it raises
    ``resource_not_found`` to match BigQuery.
    """
    existing = ctx.catalog.get_routine(ref.project_id, ref.dataset_id, ref.routine_id)
    if existing is None:
        if ref.if_exists:
            return "SKIP"
        from bqemulator.domain.errors import ResourceRef, resource_not_found

        raise resource_not_found(
            ResourceRef("routine", ref.project_id, ref.dataset_id, ref.routine_id),
        )
    async with ctx.engine.write_lock():
        registry = getattr(ctx, "udf_registry", None)
        if registry is not None:
            registry.deregister(existing, ctx.engine)
        ctx.catalog.delete_routine(
            ref.project_id,
            ref.dataset_id,
            ref.routine_id,
            not_found_ok=True,
        )
    return "DROP"


def _single_statement(script: Any) -> Any | None:
    """Return the sole statement of a parsed script, or ``None`` if not single."""
    statements = getattr(script, "statements", None)
    if statements is None or len(statements) != 1:
        return None
    return statements[0]


def _resolve_routine_ref(ref: str, default_project: str) -> tuple[str, str, str] | None:
    """Resolve a dotted CREATE-routine name to ``(project, dataset, routine)``."""
    return _parts_to_ref([p for p in ref.split(".") if p], default_project)


def _ref_from_name(raw_name: str, default_project: str) -> tuple[str, str, str] | None:
    """Resolve a possibly-backticked dotted DROP name to ``(project, dataset, routine)``."""
    cleaned = raw_name.replace("`", "").strip()
    return _parts_to_ref([p for p in cleaned.split(".") if p], default_project)


def _split_routine_table(table: exp.Table, default_project: str) -> tuple[str, str, str] | None:
    """Resolve a sqlglot ``Table`` routine target to ``(project, dataset, routine)``."""
    parts = [p for p in (table.catalog, table.db, table.name) if p]
    if len(parts) == 1 and "." in parts[0]:
        # Whole-backticked ``\`proj.ds.fn\``` lands as one dotted name.
        parts = parts[0].split(".")
    return _parts_to_ref(parts, default_project)


def _parts_to_ref(parts: list[str], default_project: str) -> tuple[str, str, str] | None:
    """Map identifier parts to ``(project, dataset, routine)``; ``None`` if not 2/3 parts.

    A single-part name resolves to a session TEMP routine, which is not a
    persistent catalog object — returning ``None`` leaves the caller's
    default classification (``SCRIPT``) in place.
    """
    if len(parts) == _REF_FULLY_QUALIFIED:
        return parts[0], parts[1], parts[2]
    if len(parts) == _REF_DATASET_QUALIFIED:
        return default_project, parts[0], parts[1]
    return None


__all__ = [
    "DropRoutineRef",
    "classify_create_routine",
    "detect_drop_routine",
    "resolve_create_routine_operation",
    "run_drop_routine",
]
