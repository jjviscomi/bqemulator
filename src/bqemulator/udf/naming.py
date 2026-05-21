"""Flat-name generator for DuckDB-registered routines.

All routines — SQL scalar, TVF, JS — register under a single flat name
``{project}__{dataset}__{routine}`` in DuckDB's main namespace. This
keeps the invocation path uniform: the table_rewriter's
``Dot(Identifier, Anonymous)`` rule rewrites any ``dataset.routine(...)``
call site into ``project__dataset__routine(...)`` regardless of which
runtime backs the routine.

BigQuery project ids may contain hyphens (``test-project``) which are
valid in quoted DuckDB identifiers but invalid in bare function-call
syntax — and SQLGlot's Anonymous-node serialiser always renders the
function name unquoted. We sidestep both problems by sanitising hyphens
to ``_h_`` before constructing the flat name, so the emitted identifier
is always bare-identifier safe.

Every component is validated through the SQL-boundary whitelist so no
user-controlled character ever reaches DuckDB's SQL parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.storage.sql_identifiers import _validate_sql_id

if TYPE_CHECKING:
    from bqemulator.catalog.models import RoutineMeta


def _sanitize(name: str) -> str:
    """Replace hyphens so the result is a bare-identifier-safe token."""
    return name.replace("-", "_h_")


def sanitize_component(name: str) -> str:
    """Public entry point — same rules as the internal sanitiser."""
    return _sanitize(name)


def qualified_routine_name(routine: RoutineMeta) -> str:
    """Return the flat DuckDB name for ``routine``."""
    project = _sanitize(_validate_sql_id(routine.project_id, "project"))
    dataset = _sanitize(_validate_sql_id(routine.dataset_id, "dataset"))
    name = _sanitize(_validate_sql_id(routine.routine_id, "routine"))
    return f"{project}__{dataset}__{name}"


def qualified_routine_name_parts(
    project_id: str,
    dataset_id: str,
    routine_id: str,
) -> str:
    """Build the flat DuckDB name from raw ids. Mirrors :func:`qualified_routine_name`."""
    project = _sanitize(_validate_sql_id(project_id, "project"))
    dataset = _sanitize(_validate_sql_id(dataset_id, "dataset"))
    name = _sanitize(_validate_sql_id(routine_id, "routine"))
    return f"{project}__{dataset}__{name}"


__all__ = [
    "qualified_routine_name",
    "qualified_routine_name_parts",
    "sanitize_component",
]
