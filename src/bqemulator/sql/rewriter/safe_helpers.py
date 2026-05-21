"""Pre-translator rewrite for BigQuery's ``SAFE.`` function prefix (ADR 0023 §1.I).

BigQuery's ``SAFE.FUNCTION(args)`` is a *generic* error-swallowing form
that wraps any function call so it returns ``NULL`` instead of raising
on a runtime error. The most common use sites are
``SAFE.LN(negative)`` / ``SAFE.SQRT(negative)`` / ``SAFE.DIVIDE(x, 0)``
— calls that would normally raise on out-of-domain arguments.

SQLGlot parses the form as a typed ``SafeFunc(inner)`` AST node but its
default BQ → DuckDB transpile emits the literal text ``SAFE.LN(...)`` —
DuckDB has no ``SAFE`` schema, so the table-rewriter then mangles
``SAFE`` into a project-qualified prefix (``test_project__SAFE__LN``)
which is not a registered scalar function.

The pre-translator unwraps ``SafeFunc(inner)`` into
``TRY(inner)`` while the AST is still in BigQuery shape. SQLGlot's
default transpile of ``TRY(...)`` to DuckDB is a passthrough (DuckDB
also exposes ``TRY(...)``) so the post-translate pipeline sees a clean
``TRY`` call and DuckDB returns ``NULL`` for the wrapped error.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def rewrite_safe_helpers(bq_sql: str) -> str:
    """Pre-translate BigQuery SQL for the ``SAFE.X`` prefix form.

    Returns the input unchanged when no rewrite is needed.

    Parse failures are tolerated: we return the original SQL so the
    downstream SQLGlot transpile surfaces its own parse error message.
    """
    if "SAFE." not in bq_sql.upper():
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = _rewrite_safe_funcs(parsed)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _rewrite_safe_funcs(tree: exp.Expression) -> bool:
    """Replace every ``SafeFunc(inner)`` with ``TRY(inner)``.

    The walk uses a snapshot list so the replacement nodes (which are
    ``Anonymous`` and not ``SafeFunc``) do not loop the iterator.
    """
    modified = False
    for node in list(tree.find_all(exp.SafeFunc)):
        inner = node.this
        if inner is None:
            continue
        replacement = exp.Anonymous(this="TRY", expressions=[inner.copy()])
        node.replace(replacement)
        modified = True
    return modified


__all__ = ["rewrite_safe_helpers"]
