"""Pre-translator rewriter for BigQuery aggregate variants DuckDB rejects.

DuckDB ships ``array_agg`` and ``string_agg`` but rejects the
``ORDER BY ... LIMIT n`` form BigQuery allows inside those aggregates,
and SQLGlot's BigQuery → DuckDB transpile silently drops the
``IGNORE NULLS`` modifier. Rewriting *before* the transpile preserves
those signals so we can emit DuckDB-compatible substitutes:

* ``ARRAY_AGG(x ORDER BY k LIMIT n)`` →
  ``ARRAY_SLICE(ARRAY_AGG(x ORDER BY k), 1, n)`` — sort first, then
  take the leading ``n`` elements. The ``array_slice(arr, 1, n)`` form
  is DuckDB's idiomatic ``LIMIT n`` on an array.

* ``STRING_AGG(x, sep ORDER BY k LIMIT n)`` →
  ``ARRAY_TO_STRING(ARRAY_SLICE(ARRAY_AGG(x ORDER BY k), 1, n), sep)``
  — same shape with a final flatten through the separator.

* ``ARRAY_AGG(expr IGNORE NULLS …)`` →
  ``ARRAY_AGG(expr …) FILTER (WHERE expr IS NOT NULL)`` — DuckDB's
  ``FILTER`` clause matches BigQuery's null-skipping contract exactly.

Each transform leaves untouched any aggregate that does not match the
specific shape — the function short-circuits on the easy path.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def rewrite_aggregate_variants(bq_sql: str) -> str:
    """Pre-translate BigQuery aggregate variants DuckDB rejects.

    Returns the input unchanged when no rewrite is needed (the common
    case). Parse failures fall through to the existing downstream
    error path.
    """
    upper = bq_sql.upper()
    if not any(token in upper for token in ("ARRAY_AGG", "STRING_AGG", "IGNORE NULLS")):
        return bq_sql
    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = False
    modified |= _rewrite_ignore_nulls(parsed)
    modified |= _rewrite_array_agg_limit(parsed)
    modified |= _rewrite_string_agg_limit(parsed)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _rewrite_ignore_nulls(tree: exp.Expression) -> bool:
    """Walk *tree* and replace ``IgnoreNulls(ArrayAgg(...))`` patterns.

    Emits ``ArrayAgg(expr) FILTER (WHERE expr IS NOT NULL)`` so the
    DuckDB output keeps the null-filtering behaviour the BigQuery
    ``IGNORE NULLS`` modifier expressed.
    """
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.IgnoreNulls):
            continue
        inner = node.this
        if not isinstance(inner, exp.ArrayAgg):
            continue
        agg_operand = inner.this
        filter_target = _filter_target(agg_operand)
        agg_copy = inner.copy()
        replacement = exp.Filter(
            this=agg_copy,
            expression=exp.Where(
                this=exp.Not(this=exp.Is(this=filter_target.copy(), expression=exp.Null())),
            ),
        )
        node.replace(replacement)
        modified = True
    return modified


def _filter_target(agg_operand: exp.Expression) -> exp.Expression:
    """Pull the value-expression out of an aggregate's inner operand.

    The aggregate may wrap its operand in an :class:`exp.Order` (when an
    ``ORDER BY`` follows the value). We need the bare value so the
    ``IS NOT NULL`` filter matches what the aggregate is summing.
    """
    if isinstance(agg_operand, exp.Order):
        return agg_operand.this
    return agg_operand


def _rewrite_array_agg_limit(tree: exp.Expression) -> bool:
    """Walk *tree* and replace ``ARRAY_AGG(x ORDER BY k LIMIT n)``."""
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.ArrayAgg):
            continue
        operand = node.this
        if not isinstance(operand, exp.Limit):
            continue
        # Unwrap the Limit so the inner agg sees only the Order.
        inner = operand.this
        limit_expr = operand.expression
        clean_agg = exp.ArrayAgg(this=inner.copy())
        replacement = exp.Anonymous(
            this="ARRAY_SLICE",
            expressions=[clean_agg, exp.Literal.number(1), limit_expr.copy()],
        )
        node.replace(replacement)
        modified = True
    return modified


def _rewrite_string_agg_limit(tree: exp.Expression) -> bool:
    """Walk *tree* and replace ``STRING_AGG(x, sep ORDER BY k LIMIT n)``."""
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.GroupConcat):
            continue
        operand = node.this
        if not isinstance(operand, exp.Limit):
            continue
        inner = operand.this
        limit_expr = operand.expression
        separator = node.args.get("separator") or exp.Literal.string(",")
        clean_agg = exp.ArrayAgg(this=inner.copy())
        sliced = exp.Anonymous(
            this="ARRAY_SLICE",
            expressions=[clean_agg, exp.Literal.number(1), limit_expr.copy()],
        )
        replacement = exp.Anonymous(
            this="ARRAY_TO_STRING",
            expressions=[sliced, separator.copy()],
        )
        node.replace(replacement)
        modified = True
    return modified


__all__ = ["rewrite_aggregate_variants"]
