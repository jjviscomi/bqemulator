"""Pre-translator rewrites for BigQuery JSON functions (ADR 0023 §1.I).

BigQuery distinguishes two JSON-producing functions:

* ``TO_JSON(value)`` → returns a ``JSON``-typed column.
* ``TO_JSON_STRING(value)`` → returns a ``STRING``-typed column.

SQLGlot parses both as the same ``JSONFormat`` AST node, with the
``to_json`` flag set for the JSON-returning variant. Its BQ → DuckDB
transpile then collapses both to ``CAST(TO_JSON(value) AS TEXT)`` —
the DuckDB ``TEXT`` cast forces a ``VARCHAR`` result regardless of
which BigQuery function was called. Both directions are then
indistinguishable in the DuckDB AST, so the JSON-vs-STRING choice has
to be captured *before* the transpile.

The rewrite wraps ``JSONFormat(to_json=True)`` in an explicit
``CAST(... AS JSON)``. SQLGlot preserves the outer cast through the
transpile, so the final DuckDB SQL is ``CAST(CAST(TO_JSON(value) AS
TEXT) AS JSON)`` and the result column carries the DuckDB ``JSON``
type. The engine's ``bqemu.duckdb_type`` metadata path then surfaces
the column as BigQuery ``JSON`` on the wire.

``TO_JSON_STRING`` (``JSONFormat`` with ``to_json`` False/missing) is
left untouched so it keeps the default ``STRING`` shape.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def rewrite_json_helpers(bq_sql: str) -> str:
    """Pre-translate BigQuery SQL for JSON functions with lossy transpiles.

    Returns the input unchanged when no rewrite is needed.

    Parse failures are tolerated: we return the original SQL so the
    downstream SQLGlot transpile surfaces its own parse error message.
    """
    upper = bq_sql.upper()
    if "TO_JSON" not in upper:
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = _rewrite_to_json_typed(parsed)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _rewrite_to_json_typed(tree: exp.Expression) -> bool:
    """Wrap ``TO_JSON(value)`` with ``CAST(... AS JSON)``.

    Only the ``to_json=True`` form fires; ``TO_JSON_STRING`` flows
    through unchanged. Calls already wrapped in an outer ``CAST(... AS
    JSON)`` are skipped so a hand-written ``CAST(TO_JSON(x) AS JSON)``
    is not double-wrapped.
    """
    modified = False
    for node in list(tree.find_all(exp.JSONFormat)):
        if not node.args.get("to_json"):
            continue
        parent = node.parent
        if isinstance(parent, exp.Cast):
            target = parent.to
            if target is not None and target.is_type(exp.DataType.Type.JSON):
                continue
        replacement = exp.Cast(this=node.copy(), to=exp.DataType.build("JSON"))
        node.replace(replacement)
        modified = True
    return modified


__all__ = ["rewrite_json_helpers"]
