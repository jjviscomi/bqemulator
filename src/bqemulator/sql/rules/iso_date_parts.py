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

* ``DATE_TRUNC(date, ISOYEAR)`` — DuckDB *does* recognise ``ISOYEAR``
  but returns a ``TIMESTAMP`` rather than the BigQuery contract's
  ``DATE``. The rule wraps the call in a ``CAST(... AS DATE)`` so the
  Arrow column type lands on ``date32`` and the REST schema renders
  the column as ``DATE`` (the BigQuery wire-format expectation).

* ``DATE_TRUNC(date, DAY | MONTH | QUARTER | YEAR)`` (ADR 0023 §1.B
  for QUARTER, §1.I for the rest) — DuckDB's ``DATE_TRUNC`` returns a
  ``TIMESTAMP`` for any input date-time type. Wrap in
  ``CAST(... AS DATE)`` when the operand is provably DATE-typed (a
  ``CAST(... AS DATE)`` or a ``CURRENT_DATE()``).

* ``DATE_TRUNC(date, WEEK)`` (ADR 0023 §1.B) — BigQuery defaults to
  the Sunday-start week; DuckDB's ``DATE_TRUNC('WEEK', x)`` is
  Monday-start. The rule replaces the call with
  ``CAST(date - INTERVAL DAYOFWEEK(date) DAY AS DATE)`` — DuckDB's
  ``DAYOFWEEK`` returns 0 for Sunday, so subtracting ``DAYOFWEEK(date)``
  days lands on the most-recent Sunday on-or-before the input.
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule

_ISOWEEK_SYNONYM = "WEEK"


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
class DateTruncIsoyearRule(TranslationRule):
    """``DATE_TRUNC(date, ISOYEAR)`` → ``CAST(DATE_TRUNC('ISOYEAR', date) AS DATE)``.

    Without the outer cast, DuckDB returns the truncated value as
    ``TIMESTAMP``; BigQuery returns ``DATE``. The cast both fixes the
    schema (Arrow ``date32``) and the wire-rendered value (no time
    component).
    """

    name = "DATE_TRUNC_ISOYEAR"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``DATE_TRUNC`` calls whose unit is ``ISOYEAR``."""
        if not isinstance(node, exp.DateTrunc):
            return False
        unit = node.args.get("unit")
        return unit is not None and _name(unit).upper() == "ISOYEAR"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the original call in ``CAST(... AS DATE)``."""
        # Copy the original tree so the post-order walker does not
        # re-visit the rewritten node and skip its children.
        inner = node.copy()
        return exp.Cast(this=inner, to=exp.DataType.build("DATE"))


_DATE_TRUNC_CALENDAR_UNITS: frozenset[str] = frozenset(
    {"DAY", "MONTH", "QUARTER", "YEAR"},
)


@register
class DateTruncCalendarUnitRule(TranslationRule):
    """``DATE_TRUNC(date, DAY|MONTH|QUARTER|YEAR)`` → ``CAST(... AS DATE)``.

    DuckDB's ``DATE_TRUNC`` returns ``TIMESTAMP`` even when the input
    is a ``DATE`` column or literal. BigQuery preserves the ``DATE``
    type. The rule only wraps the call when the operand is provably
    DATE-typed; non-DATE operands (TIMESTAMP / DATETIME) flow through
    unchanged so their wire-format type stays correct.

    ``WEEK`` is handled separately by :class:`DateTruncWeekRule` because
    BigQuery's default ``WEEK`` is Sunday-start whereas DuckDB's
    ``DATE_TRUNC('WEEK', x)`` is Monday-start — the rewrite has to
    replace the entire computation, not merely add a cast.
    """

    name = "DATE_TRUNC_CALENDAR_UNIT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``DATE_TRUNC`` calls whose unit is a calendar specifier."""
        if not isinstance(node, exp.DateTrunc):
            return False
        unit = node.args.get("unit")
        if unit is None or _name(unit).upper() not in _DATE_TRUNC_CALENDAR_UNITS:
            return False
        return _is_date_typed(node.this)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the original call in ``CAST(... AS DATE)``."""
        return exp.Cast(this=node.copy(), to=exp.DataType.build("DATE"))


@register
class DateTruncWeekRule(TranslationRule):
    """``DATE_TRUNC(date, WEEK)`` → Sunday-start truncation cast to DATE.

    BigQuery's ``DATE_TRUNC(d, WEEK)`` defaults to a Sunday-start week
    (``DATE_TRUNC(d, WEEK(MONDAY))`` would be the Monday-start form).
    DuckDB's ``DATE_TRUNC('WEEK', d)`` is Monday-start by default and
    returns ``TIMESTAMP``. We compute the Sunday on-or-before *d* with
    ``d - INTERVAL DAYOFWEEK(d) DAY`` — DuckDB's ``DAYOFWEEK`` is 0 for
    Sunday, 6 for Saturday — and cast to ``DATE`` so the wire-format
    column type matches.
    """

    name = "DATE_TRUNC_WEEK"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match plain ``DATE_TRUNC(d, WEEK)`` over a DATE-typed operand."""
        if not isinstance(node, exp.DateTrunc):
            return False
        unit = node.args.get("unit")
        if unit is None or _name(unit).upper() != "WEEK":
            return False
        return _is_date_typed(node.this)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit the Sunday-start truncation cast to DATE."""
        operand = node.this.copy()
        dayofweek = exp.Anonymous(this="DAYOFWEEK", expressions=[operand.copy()])
        interval = exp.Interval(this=dayofweek, unit=exp.Var(this="DAY"))
        sub = exp.Sub(this=operand, expression=interval)
        return exp.Cast(this=sub, to=exp.DataType.build("DATE"))


def _is_date_typed(node: exp.Expression | None) -> bool:
    """Return True when *node* is provably a DATE-typed expression.

    Covers the two BigQuery-side syntactic forms that always produce a
    DATE after the SQLGlot transpile: an explicit ``CAST(... AS DATE)``
    (which is what ``DATE '…'`` typed literals collapse to), and
    ``CURRENT_DATE()`` / ``CURRENT_DATE``. Column references and
    sub-expressions whose type the translator cannot statically
    determine fall through — the matching rule will leave the call
    alone, preserving DuckDB's default behaviour for TIMESTAMP /
    DATETIME operands.
    """
    if node is None:
        return False
    if isinstance(node, exp.Cast):
        target = node.to
        return target is not None and target.is_type(exp.DataType.Type.DATE)
    return isinstance(node, exp.CurrentDate)


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
