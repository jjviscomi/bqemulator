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
  expand it to DuckDB's ``time_bucket``, which returns ``DATE`` /
  ``TIMESTAMP`` / ``TIMESTAMPTZ`` for an operand of that type — so the
  result type is right even where the translator cannot see the
  operand's type (query parameters, ``UNNEST`` elements, script
  variables, UDF arguments) — and references the operand once.
  :class:`DateTruncIsoyearRule` handles ``ISOYEAR``, which has no fixed
  bucket width. Truncations
  that arrive as DuckDB ``DATE_TRUNC`` calls — ``TIMESTAMP_TRUNC`` /
  ``DATETIME_TRUNC`` and the time-zone and time-granularity forms of
  ``DATE_TRUNC`` — are not rewritten.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlglot import exp

from bqemulator.sql.rewriter.datetime_helpers import (
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

#: DuckDB ``DAYOFWEEK`` index of Monday, the weekday ``time_bucket``'s
#: default origin (2000-01-03) falls on for day-multiple widths.
_TIME_BUCKET_ORIGIN_WEEKDAY = 1

#: ``time_bucket`` width for each calendar granularity, as an
#: ``INTERVAL`` ``(count, unit)``. Month and year widths bucket from
#: 2000-01-01, so their buckets are calendar months, quarters and years.
_CALENDAR_BUCKET_WIDTHS: dict[str, tuple[int, str]] = {
    "DAY": (1, "DAY"),
    "MONTH": (1, "MONTH"),
    "QUARTER": (3, "MONTH"),
    "YEAR": (1, "YEAR"),
}

_ISOYEAR = frozenset({"ISOYEAR"})


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
    """``DATE_TRUNC(x, DAY|MONTH|QUARTER|YEAR)`` → ``time_bucket`` in *x*'s type.

    Expands the ``BQEMU_DATE_TRUNC(x, '<unit>')`` handoff call to
    ``time_bucket(INTERVAL <width>, x)``. DuckDB's ``DATE_TRUNC``
    returns ``TIMESTAMP`` for a ``DATE`` operand, where BigQuery returns
    ``DATE``; ``time_bucket`` returns the operand's own ``DATE`` /
    ``TIMESTAMP`` / ``TIMESTAMPTZ`` type, decided when DuckDB binds the
    query, so no translate-time operand type is needed.
    """

    name = "DATE_TRUNC_CALENDAR_UNIT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a ``BQEMU_DATE_TRUNC`` handoff call with a calendar granularity."""
        return _date_trunc_args(node, _CALENDAR_BUCKET_WIDTHS) is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``time_bucket`` with the granularity's bucket width."""
        args = _date_trunc_args(node, _CALENDAR_BUCKET_WIDTHS)
        if args is None:
            return node
        operand, unit = args
        return _time_bucket(_CALENDAR_BUCKET_WIDTHS[unit], operand)


@register
class DateTruncIsoyearRule(TranslationRule):
    """``DATE_TRUNC(x, ISOYEAR)`` → ``DATE_TRUNC('ISOYEAR', x)``, cast to DATE for a DATE operand.

    Expands the ``BQEMU_DATE_TRUNC(x, 'ISOYEAR')`` handoff call. An ISO
    year has no fixed length, so ``time_bucket`` cannot express it;
    DuckDB's ``DATE_TRUNC('ISOYEAR', x)`` computes the boundary but
    returns ``TIMESTAMP`` for a ``DATE`` operand. The call is wrapped in
    ``CAST(... AS DATE)`` when the operand is provably DATE-typed (see
    :func:`_is_date_typed`); ``TIMESTAMP`` / ``TIMESTAMPTZ`` operands
    keep DuckDB's result type, which is BigQuery's.
    """

    name = "DATE_TRUNC_ISOYEAR"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a ``BQEMU_DATE_TRUNC(x, 'ISOYEAR')`` handoff call."""
        return _date_trunc_args(node, _ISOYEAR) is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``DATE_TRUNC('ISOYEAR', x)``, cast to ``DATE`` for a DATE-typed operand."""
        args = _date_trunc_args(node, _ISOYEAR)
        if args is None:
            return node
        operand, _ = args
        truncated = exp.Anonymous(
            this="DATE_TRUNC",
            expressions=[exp.Literal.string("ISOYEAR"), operand.copy()],
        )
        if _is_date_typed(operand):
            return exp.Cast(this=truncated, to=exp.DataType.build("DATE"))
        return truncated


@register
class DateTruncWeekRule(TranslationRule):
    """``DATE_TRUNC(x, WEEK|WEEK(<WEEKDAY>)|ISOWEEK)`` → week-start ``time_bucket`` in *x*'s type.

    Expands the ``BQEMU_DATE_TRUNC(x, 'WEEK(<WEEKDAY>)')`` handoff call
    (``WEEK`` arrives as ``WEEK(SUNDAY)``, ``ISOWEEK`` as
    ``WEEK(MONDAY)``) to ``time_bucket(INTERVAL 7 DAY, x, INTERVAL
    <offset> DAY)``. Seven-day buckets start on Monday, the weekday of
    ``time_bucket``'s default origin, so the offset is the days from
    Monday to the week's first day. The result keeps the operand's
    ``DATE`` / ``TIMESTAMP`` / ``TIMESTAMPTZ`` type, and ``TIMESTAMPTZ``
    weeks are bucketed in UTC (BigQuery's default time zone).
    """

    name = "DATE_TRUNC_WEEK"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a ``BQEMU_DATE_TRUNC`` handoff call with a week granularity."""
        return _date_trunc_args(node, DATE_TRUNC_WEEK_START) is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit the week-start ``time_bucket`` for the call's operand and first day."""
        args = _date_trunc_args(node, DATE_TRUNC_WEEK_START)
        if args is None:
            return node
        operand, granularity = args
        first_day = DATE_TRUNC_WEEK_START[granularity]
        offset = (first_day - _TIME_BUCKET_ORIGIN_WEEKDAY) % _DAYS_PER_WEEK
        return _time_bucket((_DAYS_PER_WEEK, "DAY"), operand, offset_days=offset)


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


def _time_bucket(
    width: tuple[int, str],
    operand: exp.Expression,
    *,
    offset_days: int = 0,
) -> exp.Expression:
    """Build ``time_bucket(INTERVAL <width>, operand[, INTERVAL <offset_days> DAY])``."""
    count, unit = width
    arguments: list[exp.Expression] = [_interval(count, unit), operand.copy()]
    if offset_days:
        arguments.append(_interval(offset_days, "DAY"))
    return exp.Anonymous(this="TIME_BUCKET", expressions=arguments)


def _interval(count: int, unit: str) -> exp.Interval:
    """Build ``INTERVAL '<count>' <unit>``."""
    return exp.Interval(this=exp.Literal.string(str(count)), unit=exp.Var(this=unit))


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
    DATE-typed: :class:`DateTruncIsoyearRule` then keeps DuckDB's
    ``TIMESTAMP`` result, which is BigQuery's result type for DATETIME /
    TIMESTAMP operands.
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
    "DateTruncIsoyearRule",
    "DateTruncWeekRule",
    "ExtractIsoweekRule",
]
