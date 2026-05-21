"""Translation rules for BigQuery INTERVAL functions.

DuckDB has no scalar ``justify_hours`` / ``justify_days`` /
``justify_interval`` (PostgreSQL has them; DuckDB does not). We emit
a normalisation expression built from ``to_months`` / ``to_days`` /
``to_hours`` / ``to_minutes`` / ``to_microseconds`` calls plus the
``// 24`` and ``// 30`` integer-division pulls that match BigQuery's
documented JUSTIFY semantics.

Compound interval literals (``INTERVAL '1-2 3 4:5:6.789' YEAR TO
SECOND``) are handled by the *pre-translator* rewriter
:mod:`bqemulator.sql.rewriter.specialized_types` because DuckDB's
parser refuses to accept the ``YEAR TO SECOND`` form at all. The
post-translator pass here only sees JustifyHours/Days/Interval typed
nodes.
"""

from __future__ import annotations

from collections.abc import Callable

import sqlglot
from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule
from bqemulator.types.interval import (
    justify_days_expr,
    justify_hours_expr,
    justify_interval_expr,
)


def _expand_with(
    operand: exp.Expression,
    builder: Callable[[str], str],
) -> exp.Expression:
    """Render a JUSTIFY normalisation expression for *operand* and parse it back.

    ``builder`` is one of :func:`justify_hours_expr` /
    :func:`justify_days_expr` / :func:`justify_interval_expr`.
    """
    operand_sql = operand.sql(dialect="duckdb")
    expanded = builder(operand_sql)
    return sqlglot.parse_one(expanded, read="duckdb")  # type: ignore[return-value]


@register
class JustifyHoursRule(TranslationRule):
    """``JUSTIFY_HOURS(x)`` → DuckDB normalisation expression."""

    name = "JUSTIFY_HOURS"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``JustifyHours`` node."""
        return isinstance(node, exp.JustifyHours)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Expand to the per-component to_* sum that DuckDB accepts."""
        return _expand_with(node.this, justify_hours_expr)


@register
class JustifyDaysRule(TranslationRule):
    """``JUSTIFY_DAYS(x)`` → DuckDB normalisation expression."""

    name = "JUSTIFY_DAYS"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``JustifyDays`` node."""
        return isinstance(node, exp.JustifyDays)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Expand to the per-component to_* sum that DuckDB accepts."""
        return _expand_with(node.this, justify_days_expr)


@register
class JustifyIntervalRule(TranslationRule):
    """``JUSTIFY_INTERVAL(x)`` → both JUSTIFY_HOURS + JUSTIFY_DAYS rules combined."""

    name = "JUSTIFY_INTERVAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``JustifyInterval`` node."""
        return isinstance(node, exp.JustifyInterval)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Expand to the combined JUSTIFY normalisation."""
        return _expand_with(node.this, justify_interval_expr)


__all__ = [
    "JustifyDaysRule",
    "JustifyHoursRule",
    "JustifyIntervalRule",
]
