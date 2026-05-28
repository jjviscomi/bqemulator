"""``UNNEST ... WITH OFFSET`` offset rebase.

BigQuery's ``WITH OFFSET`` is 0-based; DuckDB's ``WITH ORDINALITY`` is
1-based. SQLGlot translates ``WITH OFFSET AS off`` to
``WITH ORDINALITY AS _t0(val, off)`` but preserves the 1-based semantics.

This rewriter walks the original BigQuery AST. For every ``Unnest``
with an ``offset`` column, it:

1. Records the offset column name.
2. Rewrites every ``Column`` reference that names an offset column to
   ``<col> - 1`` so the emitted DuckDB SQL sees 0-based values.

The rewrite happens on the BigQuery AST, so the subsequent SQLGlot
transpile step handles the rest of the DuckDB conversion uniformly.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def rewrite_unnest_offset(bq_sql: str) -> str:
    """Rebase ``WITH OFFSET`` columns to 0-based semantics.

    Returns the SQL unchanged if parsing fails or no ``WITH OFFSET``
    appears.
    """
    if "WITH OFFSET" not in bq_sql.upper():
        return bq_sql
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001
        return bq_sql

    offset_names = _collect_offset_names(tree)
    if not offset_names:
        return bq_sql

    if not _rebase_offset_columns(tree, offset_names):
        return bq_sql
    return tree.sql(dialect="bigquery")


def _collect_offset_names(tree: exp.Expression) -> set[str]:
    """Return every offset column name introduced by a ``WITH OFFSET`` clause.

    Falls back to ``"offset"`` for the un-aliased ``WITH OFFSET`` form
    (BigQuery's documented default).
    """
    names: set[str] = set()
    for unnest in tree.find_all(exp.Unnest):
        offset_expr = unnest.args.get("offset")
        if isinstance(offset_expr, exp.Identifier):
            names.add(offset_expr.name)
        elif offset_expr is True:
            names.add("offset")
    return names


def _rebase_offset_columns(tree: exp.Expression, offset_names: set[str]) -> bool:
    """Replace every ``Column(name in offset_names)`` with ``(col - 1)``.

    Skips the column reference that sits inside an Unnest's own offset
    slot (where it names the alias, not a usage). Preserves wrapping
    ``Alias`` nodes so ``WITH OFFSET AS off`` projections retain their
    alias on the rebased expression.

    Returns ``True`` when at least one column was rebased.
    """
    modified = False
    for col in list(tree.find_all(exp.Column)):
        if col.name not in offset_names:
            continue
        if col.find_ancestor(exp.Unnest) is not None and col.parent_select is None:
            continue
        replacement = exp.Paren(this=exp.Sub(this=col.copy(), expression=exp.Literal.number(1)))
        parent = col.parent
        if isinstance(parent, exp.Alias):
            parent.set("this", replacement)
        else:
            col.replace(replacement)
        modified = True
    return modified


__all__ = ["rewrite_unnest_offset"]
