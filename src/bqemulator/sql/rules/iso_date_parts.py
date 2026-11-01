"""Translation rules for BigQuery ISO + calendar date-part keywords.

BigQuery's ``ISOWEEK`` and ``ISOYEAR`` date parts power the ISO 8601
week-and-year calendar; ``DAY`` / ``MONTH`` / ``QUARTER`` / ``YEAR`` /
``WEEK`` follow the Gregorian calendar with the Sunday-start week
convention. DuckDB supports the underlying semantics but its parser
recognises only the unqualified keywords and its ``DATE_TRUNC`` widens
``DATE`` inputs to ``TIMESTAMP`` outputs:

* ``EXTRACT(ISOWEEK FROM x)`` — DuckDB rejects ``ISOWEEK`` as an
  extract specifier even though ``EXTRACT(WEEK FROM x)`` already
  returns the ISO 8601 week number (1-53). The rule rewrites the
  specifier from ``ISOWEEK`` to ``WEEK`` so the underlying ISO
  computation runs unchanged.

* ``DATE_TRUNC(x, <date granularity>)`` (ADR 0023 §1.B, §1.I) —
  BigQuery returns *x*'s own type, so a DATE operand yields ``DATE``
  where DuckDB's ``DATE_TRUNC`` yields ``TIMESTAMP``; its ``WEEK``
  starts on Sunday, ``WEEK(<WEEKDAY>)`` on the named day, and
  ``ISOWEEK`` on Monday, where DuckDB's ``'WEEK'`` is Monday-start
  only. After the SQLGlot transpile a ``DATE_TRUNC`` is
  indistinguishable from ``TIMESTAMP_TRUNC`` / ``DATETIME_TRUNC``
  (which return ``DATETIME`` for a DATE operand) and its week start is
  lost, so
  :func:`~bqemulator.sql.rewriter.datetime_helpers.rewrite_datetime_helpers`
  carries each call across as ``BQEMU_DATE_TRUNC(x, '<granularity>')``.
  :class:`DateTruncCalendarUnitRule` and :class:`DateTruncWeekRule`
  expand that call here, preserving the operand's type. Truncations
  that arrive as DuckDB ``DATE_TRUNC`` calls — ``TIMESTAMP_TRUNC`` /
  ``DATETIME_TRUNC`` and the time-zone and time-granularity forms of
  ``DATE_TRUNC`` — are not rewritten.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlglot import exp

from bqemulator.sql.rewriter.datetime_helpers import (
    DATE_TRUNC_CALENDAR_UNITS,
    DATE_TRUNC_FUNCTION,
    DATE_TRUNC_WEEK_START,
)
from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule

if TYPE_CHECKING:
    from collections.abc import Container

_ISOWEEK_SYNONYM = "WEEK"

#: Days in a week — the modulus of the week-start offset arithmetic.
_DAYS_PER_WEEK = 7


@register
class ExtractIsoweekRule(TranslationRule):
    """``EXTRACT(ISOWEEK FROM x)`` → ``EXTRACT(WEEK FROM x)``."""

    name = "EXTRACT_ISOWEEK"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``EXTRACT`` calls whose specifier is ``ISOWEEK`` (case-insensitive)."""
        if not isinstance(node, exp.Extract):
            return False
        specifier = node.this
        return _name(specifier).upper() == "ISOWEEK"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace the ``ISOWEEK`` specifier with ``WEEK``."""
        replacement = exp.Var(this=_ISOWEEK_SYNONYM)
        operand = node.expression
        return exp.Extract(this=replacement, expression=operand.copy())


@register
class DateTruncCalendarUnitRule(TranslationRule):
    """``DATE_TRUNC(x, DAY|MONTH|QUARTER|YEAR|ISOYEAR)`` → ``DATE_TRUNC`` in *x*'s type.

    Expands the ``BQEMU_DATE_TRUNC(x, '<unit>')`` handoff call. DuckDB's
    ``DATE_TRUNC`` computes each of these granularities under the same
    name but returns ``TIMESTAMP`` for a ``DATE`` operand, where
    BigQuery returns ``DATE``. The call is wrapped in
    ``CAST(... AS DATE)`` when the operand is DATE-typed, so the Arrow
    column lands on ``date32`` and the REST schema renders ``DATE``;
    DuckDB's result for a ``TIMESTAMP`` / ``TIMESTAMPTZ`` operand is
    already BigQuery's ``DATETIME`` / ``TIMESTAMP``.
    """

    name = "DATE_TRUNC_CALENDAR_UNIT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a ``BQEMU_DATE_TRUNC`` handoff call with a calendar granularity."""
        return _date_trunc_args(node, DATE_TRUNC_CALENDAR_UNITS) is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit DuckDB's ``DATE_TRUNC``, cast to ``DATE`` for a DATE-typed operand."""
        args = _date_trunc_args(node, DATE_TRUNC_CALENDAR_UNITS)
        if args is None:
            return node
        operand, unit = args
        truncated = exp.Anonymous(
            this="DATE_TRUNC",
            expressions=[exp.Literal.string(unit), operand.copy()],
        )
        if _is_date_typed(operand):
            return exp.Cast(this=truncated, to=exp.DataType.build("DATE"))
        return truncated


@register
class DateTruncWeekRule(TranslationRule):
    """``DATE_TRUNC(x, WEEK|WEEK(<WEEKDAY>)|ISOWEEK)`` → week-start truncation in *x*'s type.

    Expands the ``BQEMU_DATE_TRUNC(x, 'WEEK(<WEEKDAY>)')`` handoff call
    (``WEEK`` arrives as ``WEEK(SUNDAY)``, ``ISOWEEK`` as
    ``WEEK(MONDAY)``). The truncation subtracts the days elapsed since
    the week's first day, ``offset = (DAYOFWEEK(x) - first_day) mod 7``,
    where DuckDB's ``DAYOFWEEK`` is Sunday = 0 … Saturday = 6:

    * DATE-typed *x* → ``CAST(x - INTERVAL (offset) DAY AS DATE)``;
      BigQuery returns ``DATE`` and DuckDB's ``DATE - INTERVAL`` widens
      to ``TIMESTAMP``.
    * any other *x* (DATETIME / TIMESTAMP, or a type the translator
      cannot determine) → ``DATE_TRUNC('DAY', x) - INTERVAL (offset)
      DAY``, which keeps DuckDB's ``TIMESTAMP`` / ``TIMESTAMPTZ`` —
      BigQuery's ``DATETIME`` / ``TIMESTAMP`` result types. DuckDB
      evaluates ``TIMESTAMPTZ`` week boundaries in the session time
      zone, which the engine pins to UTC (BigQuery's default).
    """

    name = "DATE_TRUNC_WEEK"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a ``BQEMU_DATE_TRUNC`` handoff call with a week granularity."""
        return _date_trunc_args(node, DATE_TRUNC_WEEK_START) is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit the week-start truncation for the call's operand and first day."""
        args = _date_trunc_args(node, DATE_TRUNC_WEEK_START)
        if args is None:
            return node
        operand, granularity = args
        offset = _days_since_week_start(operand, DATE_TRUNC_WEEK_START[granularity])
        interval = exp.Interval(this=exp.Paren(this=offset), unit=exp.Var(this="DAY"))
        if _is_date_typed(operand):
            sub = exp.Sub(this=operand.copy(), expression=interval)
            return exp.Cast(this=sub, to=exp.DataType.build("DATE"))
        day_start = exp.Anonymous(
            this="DATE_TRUNC",
            expressions=[exp.Literal.string("DAY"), operand.copy()],
        )
        return exp.Sub(this=day_start, expression=interval)


def _date_trunc_args(
    node: exp.Expression,
    granularities: Container[str],
) -> tuple[exp.Expression, str] | None:
    """Return ``(operand, granularity)`` for a handoff call in *granularities*, else ``None``.

    A well-formed ``BQEMU_DATE_TRUNC`` call carries exactly an operand
    and a string-literal granularity.
    """
    if not isinstance(node, exp.Anonymous) or str(node.this).upper() != DATE_TRUNC_FUNCTION:
        return None
    if len(node.expressions) != 2:  # noqa: PLR2004 — (operand, granularity)
        return None
    operand, granularity = node.expressions
    if not isinstance(granularity, exp.Literal) or not granularity.is_string:
        return None
    name = str(granularity.this).upper()
    if name not in granularities:
        return None
    return operand, name


def _days_since_week_start(operand: exp.Expression, first_day: int) -> exp.Expression:
    """Build ``(DAYOFWEEK(operand) - first_day) mod 7`` as a non-negative DuckDB expression.

    DuckDB's ``DAYOFWEEK`` is 0 (Sunday) … 6 (Saturday). For a Sunday
    week start the offset is ``DAYOFWEEK`` itself; otherwise adding
    ``7 - first_day`` before the modulus keeps the dividend
    non-negative.
    """
    dayofweek = exp.Anonymous(this="DAYOFWEEK", expressions=[operand.copy()])
    if first_day == 0:
        return dayofweek
    shifted = exp.Add(this=dayofweek, expression=exp.Literal.number(_DAYS_PER_WEEK - first_day))
    return exp.Mod(this=exp.Paren(this=shifted), expression=exp.Literal.number(_DAYS_PER_WEEK))


def _is_date_typed(node: exp.Expression | None) -> bool:
    """Return True when *node* is provably a DATE-typed expression.

    Recognises three forms: an explicit ``CAST(... AS DATE)`` (which
    is what ``DATE '…'`` typed literals collapse to), ``CURRENT_DATE()``
    / ``CURRENT_DATE``, and — when the caller ran the translator with a
    ``schema`` — the operand's annotated ``.type``. The translator's
    annotation pass (``SQLTranslator._annotate_tree``) resolves
    table/column references against that schema and types operators
    the way DuckDB evaluates them, so a schema-declared DATE column
    (e.g. ``pl.placed_on`` in a JOINed/aliased query) is DATE-typed
    while ``date + INTERVAL …`` — and any CTE column derived from it —
    is ``TIMESTAMP``.

    An operand whose type the translator cannot statically determine
    (no schema supplied, or annotation failed for that query) is not
    DATE-typed: the rules keep DuckDB's ``TIMESTAMP`` result, which is
    BigQuery's result type for DATETIME / TIMESTAMP operands.
    """
    if node is None:
        return False
    if isinstance(node, exp.Cast):
        target = node.to
        return target is not None and target.is_type(exp.DataType.Type.DATE)
    if isinstance(node, exp.CurrentDate):
        return True
    annotated = getattr(node, "type", None)
    return annotated is not None and annotated.is_type(exp.DataType.Type.DATE)


def _name(node: exp.Expression) -> str:
    """Return *node*'s text content (handles ``Var`` / ``Literal`` / ``Identifier``)."""
    if isinstance(node, exp.Literal):
        return str(node.this)
    return node.name or str(node.this or "")


__all__ = [
    "DateTruncCalendarUnitRule",
    "DateTruncWeekRule",
    "ExtractIsoweekRule",
]
