"""Pre-translator rewrites for BigQuery date/time functions (ADR 0023 §1.I).

Some BigQuery date/time forms are *lossy* under SQLGlot's BigQuery →
DuckDB transpile: by the time the AST is in DuckDB form the original
shape (and hence the right rewrite target) is gone. The transforms
below run while the AST is still in BigQuery shape so the downstream
SQLGlot transpile + the post-translator rules in
:mod:`bqemulator.sql.rules.datetime_semantics` produce the BigQuery
semantic.

Currently handled:

* ``LAST_DAY(x, WEEK)`` — BigQuery returns the *Saturday* that closes
  the Sunday-start week containing *x*. SQLGlot's transpile inlines
  the call to a ``CAST(x + INTERVAL ((7 - DAYOFWEEK(x)) % 7) DAY AS
  DATE)`` expression that lands on *Sunday* (because DuckDB's
  ``DAYOFWEEK`` is 0-indexed with Sunday = 0). We rewrite the BigQuery
  AST node to ``DATE_ADD(x, INTERVAL (7 - EXTRACT(DAYOFWEEK FROM x))
  DAY)`` — BigQuery's ``DAYOFWEEK`` is 1-indexed (Sun = 1, Sat = 7), so
  ``7 - DAYOFWEEK`` gives the correct offset and the downstream
  ``ExtractDayofweekRule`` adjusts the DuckDB-side EXTRACT to match.

* ``DATE_ADD(date, INTERVAL n DAY)`` / ``DATE_SUB(date, INTERVAL n DAY)``
  / ``DATE_FROM_UNIX_DATE(n)`` — BigQuery's *function-call* forms
  return ``DATE``; SQLGlot transpiles them to ``date + INTERVAL`` in
  DuckDB, which widens to ``TIMESTAMP``. The BigQuery *operator* forms
  ``date + INTERVAL n DAY`` return ``DATETIME`` and must NOT be cast.
  Both forms produce the same shape after the SQLGlot transpile (an
  ``Add``/``Sub`` over the operands), so we wrap the *function-call*
  variants in an explicit ``CAST(... AS DATE)`` *before* the transpile.
  The cast survives the transpile, preserving the DATE wire type.

* ``TIMESTAMP_MICROS(n)`` / ``TIMESTAMP_MILLIS(n)`` — BigQuery returns
  ``TIMESTAMP`` (a UTC instant). SQLGlot transpiles them to DuckDB's
  ``MAKE_TIMESTAMP(n)`` / ``EPOCH_MS(n)``, both of which return a
  *naive* ``TIMESTAMP`` (no timezone). The wire-format renderer then
  surfaces a naive value as ``DATETIME``. We pre-translate the BigQuery
  AST node ``UnixToTime`` (the only shape that carries the int → ts
  direction at BQ level) to a ``TIMESTAMP_ADD(TIMESTAMP '1970-01-01
  00:00:00+00', INTERVAL n MICROSECOND|MILLISECOND)`` expression that
  transpiles to a TIMESTAMPTZ-typed sum.
  ``TIMESTAMP_SECONDS(n)`` is *not* rewritten because SQLGlot transpiles
  it to DuckDB's ``TO_TIMESTAMP(n)`` which already returns TIMESTAMPTZ.

LAST_DAY with the other date-part specifiers (``MONTH`` / ``QUARTER``
/ ``YEAR`` / ``ISOYEAR``) is handled correctly by SQLGlot's default
transpile and is not touched here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

if TYPE_CHECKING:
    from collections.abc import Callable

_DATE_RETURNING_FUNCTIONS = (exp.DateAdd, exp.DateSub, exp.DateFromUnixDate)


def rewrite_datetime_helpers(bq_sql: str) -> str:
    """Pre-translate BigQuery SQL for date/time functions with lossy transpiles.

    Returns the input unchanged when no rewrite is needed (the common
    case for queries without ``LAST_DAY(..., WEEK)`` /
    ``DATE_ADD`` / ``DATE_SUB`` / ``DATE_FROM_UNIX_DATE`` /
    ``TIMESTAMP_MICROS`` / ``TIMESTAMP_MILLIS``).

    Parse failures are tolerated: we return the original SQL so the
    downstream SQLGlot transpile surfaces its own parse error message.
    """
    needs = _detect_rewrite_needs(bq_sql.upper())
    if not any(needs.values()):
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = False
    for key, rewriter in _DATETIME_REWRITE_PASSES:
        if needs[key]:
            modified |= rewriter(parsed)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _detect_rewrite_needs(upper_sql: str) -> dict[str, bool]:
    """Return a ``{pass_key: bool}`` map of which datetime passes are needed.

    Splitting the cheap-but-branchy marker checks out of the main
    entry-point keeps that function's cyclomatic complexity low; the
    string scans here are still ``O(len(sql))`` and run only once.
    """
    return {
        "last_day_week": "LAST_DAY" in upper_sql and "WEEK" in upper_sql,
        "date_cast": (
            "DATE_ADD" in upper_sql or "DATE_SUB" in upper_sql or "DATE_FROM_UNIX_DATE" in upper_sql
        ),
        "timestamp_micros_millis": (
            "TIMESTAMP_MICROS" in upper_sql or "TIMESTAMP_MILLIS" in upper_sql
        ),
    }


def _rewrite_date_function_results(tree: exp.Expression) -> bool:
    """Wrap ``DATE_ADD`` / ``DATE_SUB`` / ``DATE_FROM_UNIX_DATE`` in CAST AS DATE.

    The wrap only fires for the function-call forms, never for the
    ``date + INTERVAL`` operator form. SQLGlot transpiles both to the
    same DuckDB ``Add``/``Sub``, but BigQuery returns ``DATE`` only for
    the function-call form. The explicit ``CAST(... AS DATE)`` is
    preserved through the transpile so the result column lands on the
    wire as ``DATE``.

    The walk excludes calls that already sit inside an outer
    ``Cast(... AS DATE)`` so a hand-written ``CAST(DATE_ADD(...) AS
    DATE)`` is not double-wrapped.
    """
    modified = False
    for node in list(tree.find_all(*_DATE_RETURNING_FUNCTIONS)):
        parent = node.parent
        if isinstance(parent, exp.Cast):
            target = parent.to
            if target is not None and target.is_type(exp.DataType.Type.DATE):
                continue
        replacement = exp.Cast(this=node.copy(), to=exp.DataType.build("DATE"))
        node.replace(replacement)
        modified = True
    return modified


def _rewrite_timestamp_micros_millis(tree: exp.Expression) -> bool:
    """Rewrite ``TIMESTAMP_MICROS(n)`` / ``TIMESTAMP_MILLIS(n)`` to TIMESTAMP_ADD.

    The BigQuery AST node ``UnixToTime`` is the unambiguous int → ts
    direction (BQ's ``UNIX_MICROS`` / ``UNIX_MILLIS`` / ``UNIX_SECONDS``
    are separate ``UnixMicros`` / ``UnixMillis`` / ``UnixSeconds``
    nodes). We dispatch on the ``scale`` arg:

    * ``scale=6`` → ``TIMESTAMP_MICROS`` → rewrite to
      ``TIMESTAMP_ADD(TIMESTAMP '1970-01-01 00:00:00+00', INTERVAL n
      MICROSECOND)``.
    * ``scale=3`` → ``TIMESTAMP_MILLIS`` → ``MILLISECOND`` interval.
    * ``scale=0`` (or missing) → ``TIMESTAMP_SECONDS`` — leave alone;
      DuckDB's ``TO_TIMESTAMP(n)`` already returns TIMESTAMPTZ.
    """
    modified = False
    for node in list(tree.find_all(exp.UnixToTime)):
        scale = node.args.get("scale")
        scale_val = _literal_int(scale)
        if scale_val == 6:  # noqa: PLR2004 — TIMESTAMP_MICROS scale marker
            unit = "MICROSECOND"
        elif scale_val == 3:  # noqa: PLR2004 — TIMESTAMP_MILLIS scale marker
            unit = "MILLISECOND"
        else:
            continue
        operand = node.this
        if operand is None:
            continue
        replacement = _build_timestamp_from_epoch(operand, unit)
        node.replace(replacement)
        modified = True
    return modified


def _build_timestamp_from_epoch(operand: exp.Expression, unit: str) -> exp.Expression:
    """Construct ``TIMESTAMP_ADD(TIMESTAMP '1970-01-01 00:00:00+00', INTERVAL n unit)``.

    BigQuery's ``TIMESTAMP '1970-01-01 00:00:00+00'`` typed literal is
    the UTC epoch. ``TIMESTAMP_ADD(epoch, INTERVAL n MICROSECOND)``
    transpiles to DuckDB ``CAST('1970-01-01 00:00:00+00' AS
    TIMESTAMPTZ) + INTERVAL n MICROSECOND`` — both sides are TIMESTAMPTZ
    so the result Arrow column carries a timezone and the renderer
    emits microseconds-since-epoch the BigQuery Python client decodes
    as a UTC ``datetime``.
    """
    epoch_literal = exp.Cast(
        this=exp.Literal.string("1970-01-01 00:00:00+00"),
        to=exp.DataType.build("TIMESTAMPTZ"),
    )
    return exp.TimestampAdd(
        this=epoch_literal,
        expression=operand.copy(),
        unit=exp.Var(this=unit),
    )


def _literal_int(node: exp.Expression | None) -> int | None:
    """Return the integer value of a ``Literal(int)`` node, or ``None``."""
    if not isinstance(node, exp.Literal):
        return None
    if node.is_string:
        try:
            return int(str(node.this))
        except ValueError:
            return None
    try:
        return int(str(node.this))
    except ValueError:
        return None


def _rewrite_last_day_week(tree: exp.Expression) -> bool:
    """Replace every ``LastDay(x, WEEK)`` with the BigQuery semantic.

    Returns ``True`` when at least one replacement occurred.
    """
    modified = False
    for node in list(tree.find_all(exp.LastDay)):
        unit = node.args.get("unit")
        if unit is None or _name(unit).upper() != "WEEK":
            continue
        operand = node.this
        if operand is None:
            continue
        replacement = _build_week_saturday(operand)
        node.replace(replacement)
        modified = True
    return modified


def _build_week_saturday(operand: exp.Expression) -> exp.Expression:
    """Construct ``CAST(DATE_ADD(operand, INTERVAL (7 - DAYOFWEEK(operand)) DAY) AS DATE)``.

    BigQuery's ``DAYOFWEEK`` is 1 (Sunday) through 7 (Saturday); the
    offset to Saturday is ``7 - DAYOFWEEK``. For a Saturday input
    (DAYOFWEEK = 7) the offset is 0; for a Sunday input it is 6.

    The offset is passed as the plain ``Sub`` expression — SQLGlot's
    ``DateAdd`` builds the surrounding ``INTERVAL … DAY`` from the
    ``unit`` arg. Wrapping the offset in an explicit ``exp.Interval``
    would produce ``INTERVAL (INTERVAL N DAY) DAY`` after a round-trip
    through the BigQuery serializer.

    The outer ``CAST(... AS DATE)`` preserves BigQuery's ``LAST_DAY``
    return type (``DATE``) — without it the SQLGlot transpile would
    leave the result as the natural ``date + INTERVAL = TIMESTAMP``
    widening.
    """
    extract_dow = exp.Extract(
        this=exp.Var(this="DAYOFWEEK"),
        expression=operand.copy(),
    )
    offset = exp.Paren(
        this=exp.Sub(this=exp.Literal.number(7), expression=extract_dow),
    )
    date_add = exp.DateAdd(
        this=operand.copy(),
        expression=offset,
        unit=exp.Var(this="DAY"),
    )
    return exp.Cast(this=date_add, to=exp.DataType.build("DATE"))


def _name(node: exp.Expression) -> str:
    """Return *node*'s text content (handles ``Var`` / ``Literal`` / ``Identifier``)."""
    if isinstance(node, exp.Literal):
        return str(node.this)
    return node.name or str(node.this or "")


#: Ordered dispatch of (needs-key, rewriter) pairs. Matches the order
#: of ``_detect_rewrite_needs``'s output keys. Order is presentation-
#: only — each rewriter targets a distinct AST shape so ordering does
#: not affect the outcome.
_DATETIME_REWRITE_PASSES: tuple[tuple[str, Callable[[exp.Expression], bool]], ...] = (
    ("last_day_week", _rewrite_last_day_week),
    ("date_cast", _rewrite_date_function_results),
    ("timestamp_micros_millis", _rewrite_timestamp_micros_millis),
)


__all__ = ["rewrite_datetime_helpers"]
