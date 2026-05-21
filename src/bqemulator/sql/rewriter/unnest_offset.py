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

    offset_names: set[str] = set()
    for unnest in tree.find_all(exp.Unnest):
        offset_expr = unnest.args.get("offset")
        if isinstance(offset_expr, exp.Identifier):
            offset_names.add(offset_expr.name)
        elif offset_expr is True:
            # BigQuery allows ``WITH OFFSET`` without a name; default is
            # ``offset``. We handle that case too.
            offset_names.add("offset")

    if not offset_names:
        return bq_sql

    modified = False
    for col in list(tree.find_all(exp.Column)):
        if col.name not in offset_names:
            continue
        # Skip the Column that sits inside the Unnest's own offset slot.
        if col.find_ancestor(exp.Unnest) is not None and col.parent_select is None:
            continue
        # Replace ``col`` with ``col - 1``.
        parent = col.parent
        replacement = exp.Paren(this=exp.Sub(this=col.copy(), expression=exp.Literal.number(1)))
        # Preserve aliasing: if the column sits inside an Alias, leave the
        # alias unchanged but swap its inner expression.
        if isinstance(parent, exp.Alias):
            parent.set("this", replacement)
        else:
            col.replace(replacement)
        modified = True

    if not modified:
        return bq_sql
    return tree.sql(dialect="bigquery")


__all__ = ["rewrite_unnest_offset"]
