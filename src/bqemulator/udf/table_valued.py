"""Table-valued function runtime.

Materializes a BigQuery TVF as a DuckDB ``CREATE OR REPLACE MACRO ... AS
TABLE (body)`` under the ``{project}__{dataset}`` schema. The body is a
full SELECT, not an expression.

Example::

    # Input routine:
    #   CREATE TABLE FUNCTION acme.ds.recent_events(cutoff TIMESTAMP)
    #   AS (SELECT * FROM events WHERE ts > cutoff)
    # Emitted DuckDB DDL:
    #   CREATE OR REPLACE MACRO "acme__ds"."recent_events"(cutoff)
    #   AS TABLE (SELECT * FROM "acme__ds"."events" WHERE ts > cutoff)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.domain.result import Err, Ok
from bqemulator.observability.logging_ import get_logger
from bqemulator.sql.table_rewriter import rewrite_table_refs
from bqemulator.sql.translator import SQLTranslator
from bqemulator.storage.sql_identifiers import _validate_sql_id
from bqemulator.udf.naming import qualified_routine_name

if TYPE_CHECKING:
    from bqemulator.catalog.models import RoutineMeta
    from bqemulator.storage.engine import DuckDBEngine

_log = get_logger(__name__)


class TableValuedRuntime:
    """Table-valued function runtime."""

    def __init__(self) -> None:
        self._translator = SQLTranslator()

    def materialize(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Emit ``CREATE OR REPLACE MACRO ... AS TABLE``."""
        name = qualified_routine_name(routine)
        arg_list = ", ".join(_validate_sql_id(a.name, "routine arg") for a in routine.arguments)

        body = _translate_tvf_body(
            routine.definition_body,
            routine.project_id,
            self._translator,
        )
        ddl = f'CREATE OR REPLACE MACRO "{name}"({arg_list}) AS TABLE ({body})'
        _log.debug("tvf.create_macro", routine=routine.routine_id)
        engine.execute(ddl)

    def deregister(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Drop the table macro if it exists."""
        name = qualified_routine_name(routine)
        engine.execute(f'DROP MACRO TABLE IF EXISTS "{name}"')


def _translate_tvf_body(bq_sql: str, project_id: str, translator: SQLTranslator) -> str:
    """Translate a TVF body SELECT and apply table-reference rewriting."""
    match translator.translate(bq_sql):
        case Ok(duckdb_sql):
            pass
        case Err(error):
            raise InvalidQueryError(f"Invalid TVF body: {error.message}") from error
    return rewrite_table_refs(duckdb_sql, project_id)


__all__ = ["TableValuedRuntime"]
