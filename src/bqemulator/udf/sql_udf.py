"""SQL scalar UDF runtime.

Materializes a BigQuery SQL UDF as a DuckDB ``CREATE OR REPLACE MACRO``
under the ``{project}__{dataset}`` schema. The body is translated
through the existing :class:`SQLTranslator` so BigQuery built-ins
transpile correctly.

Example::

    # Input routine:
    #   CREATE FUNCTION acme.ds.add_one(x INT64) AS (x + 1)
    # Emitted DuckDB DDL:
    #   CREATE OR REPLACE MACRO "acme__ds"."add_one"(x) AS (x + 1)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.domain.result import Err, Ok
from bqemulator.observability.logging_ import get_logger
from bqemulator.sql.translator import SQLTranslator
from bqemulator.storage.sql_identifiers import _validate_sql_id
from bqemulator.udf.naming import qualified_routine_name

if TYPE_CHECKING:
    from bqemulator.catalog.models import RoutineMeta
    from bqemulator.storage.engine import DuckDBEngine

_log = get_logger(__name__)


class SQLUDFRuntime:
    """SQL scalar UDF runtime."""

    def __init__(self) -> None:
        self._translator = SQLTranslator()

    def materialize(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Emit ``CREATE OR REPLACE MACRO`` for the routine."""
        name = qualified_routine_name(routine)
        arg_list = ", ".join(_validate_sql_id(a.name, "routine arg") for a in routine.arguments)

        body = _translate_body(routine.definition_body, self._translator)
        ddl = f'CREATE OR REPLACE MACRO "{name}"({arg_list}) AS ({body})'
        _log.debug("sql_udf.create_macro", routine=routine.routine_id)
        engine.execute(ddl)

    def deregister(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Drop the macro if it exists."""
        name = qualified_routine_name(routine)
        engine.execute(f'DROP MACRO IF EXISTS "{name}"')


def _translate_body(bq_sql: str, translator: SQLTranslator) -> str:
    """Translate a UDF body; raise on failure.

    UDF bodies arrive as a SQL *expression* (e.g. ``x + 1``), but the
    translator expects a full statement. We wrap the body in a
    ``SELECT`` so SQLGlot parses it, then strip the wrapper.
    """
    wrapped = f"SELECT {bq_sql}"
    match translator.translate(wrapped):
        case Ok(duckdb_sql):
            pass
        case Err(error):
            raise InvalidQueryError(f"Invalid UDF body: {error.message}") from error

    # Strip the leading "SELECT " that the translator preserved.
    stripped = duckdb_sql.lstrip()
    upper = stripped.upper()
    if upper.startswith("SELECT "):
        return stripped[7:]
    return duckdb_sql


__all__ = ["SQLUDFRuntime"]
