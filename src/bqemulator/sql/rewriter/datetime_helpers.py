"""BigQuery-AST rewrites for date/time functions (ADR 0023 §1.I).

Some BigQuery date/time forms are *lossy* under SQLGlot's BigQuery →
DuckDB transpile: by the time the AST is in DuckDB form the original
shape (and hence the right rewrite target) is gone. The transforms
below rewrite each parsed BigQuery statement in place, immediately
before :class:`~bqemulator.sql.translator.SQLTranslator` generates its
DuckDB SQL, so the DuckDB generator + the post-translator rules in
:mod:`bqemulator.sql.rules.datetime_semantics` and
:mod:`bqemulator.sql.rules.iso_date_parts` produce the BigQuery
semantic. They operate on the AST the generator reads rather than on
SQL text: re-serialising BigQuery SQL is itself lossy (SQLGlot's
BigQuery generator strips ``CAST`` type parameters, prints
``WEEK(SUNDAY)`` as ``WEEK``, and reshapes ``AT TIME ZONE`` and
``VALUES … AS v(…)`` clauses), so a text rewrite would change parts of
the statement it never meant to touch.

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
  variants in an explicit ``CAST(... AS DATE)`` *before* DuckDB
  generation. The cast survives the transpile, preserving the DATE wire
  type.

* ``DATE_TRUNC(x, <date granularity>)`` — BigQuery returns *x*'s own
  type (``DATE`` for a DATE operand), starts ``WEEK`` on Sunday (it is
  ``WEEK(SUNDAY)``), ``WEEK(<WEEKDAY>)`` on the named day, and
  ``ISOWEEK`` on Monday. SQLGlot transpiles it to DuckDB's
  ``DATE_TRUNC('<unit>', x)`` — the same call it emits for
  ``TIMESTAMP_TRUNC`` / ``DATETIME_TRUNC``, which return ``DATETIME``
  for a DATE operand — collapses every ``WEEK`` / ``WEEK(<WEEKDAY>)``
  to DuckDB's Monday-start ``'WEEK'``, and passes ``ISOWEEK`` through
  as a specifier DuckDB rejects. Neither the function nor the week
  start can be recovered after the transpile, so each call with a date
  granularity and no time-zone argument becomes
  ``BQEMU_DATE_TRUNC(x, '<granularity>')``: ``DAY`` / ``MONTH`` /
  ``QUARTER`` / ``YEAR`` / ``ISOYEAR`` as written, and every week
  granularity as ``WEEK(<WEEKDAY>)`` (``WEEK`` → ``WEEK(SUNDAY)``,
  ``ISOWEEK`` → ``WEEK(MONDAY)``). The DuckDB generator emits the call
  verbatim, and the :mod:`~bqemulator.sql.rules.iso_date_parts` rules
  expand it once operand types are annotated.

* ``TIMESTAMP_MICROS(n)`` / ``TIMESTAMP_MILLIS(n)`` — BigQuery returns
  ``TIMESTAMP`` (a UTC instant). SQLGlot transpiles them to DuckDB's
  ``MAKE_TIMESTAMP(n)`` / ``EPOCH_MS(n)``, both of which return a
  *naive* ``TIMESTAMP`` (no timezone). The wire-format renderer then
  surfaces a naive value as ``DATETIME``. We rewrite the BigQuery
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

from sqlglot import exp

if TYPE_CHECKING:
    from collections.abc import Callable

_DATE_RETURNING_FUNCTIONS = (exp.DateAdd, exp.DateSub, exp.DateFromUnixDate)

#: Name of the call that carries a BigQuery ``DATE_TRUNC`` from these
#: rewrites to the :mod:`~bqemulator.sql.rules.iso_date_parts` rules:
#: ``BQEMU_DATE_TRUNC(x, '<granularity>')``.
DATE_TRUNC_FUNCTION = "BQEMU_DATE_TRUNC"

#: Handoff granularities DuckDB's ``DATE_TRUNC`` computes under the same
#: name and with the same boundaries as BigQuery.
DATE_TRUNC_CALENDAR_UNITS: frozenset[str] = frozenset(
    {"DAY", "MONTH", "QUARTER", "YEAR", "ISOYEAR"},
)

#: BigQuery weekday names in DuckDB ``DAYOFWEEK`` order (Sunday = 0 …
#: Saturday = 6).
_WEEKDAYS = ("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")

#: Handoff week granularities, each mapped to the DuckDB ``DAYOFWEEK``
#: index of the week's first day.
DATE_TRUNC_WEEK_START: dict[str, int] = {
    f"WEEK({weekday})": index for index, weekday in enumerate(_WEEKDAYS)
}

#: Week granularities written without a weekday, as their
#: ``WEEK(<WEEKDAY>)`` form: ``WEEK`` is ``WEEK(SUNDAY)``; ``ISOWEEK``
#: weeks start on Monday.
_IMPLICIT_WEEK_GRANULARITY: dict[str, str] = {"WEEK": "WEEK(SUNDAY)", "ISOWEEK": "WEEK(MONDAY)"}


def rewrite_datetime_helpers(statement: exp.Expr) -> exp.Expr:
    """Rewrite BigQuery date/time calls whose semantics the DuckDB transpile loses.

    Applies every pass to *statement* — one parsed BigQuery statement —
    in place and returns it. Each pass targets a distinct node type, so
    a statement without ``LAST_DAY(..., WEEK)`` /
    ``DATE_TRUNC(..., <date granularity>)`` / ``DATE_ADD`` /
    ``DATE_SUB`` / ``DATE_FROM_UNIX_DATE`` / ``TIMESTAMP_MICROS`` /
    ``TIMESTAMP_MILLIS`` comes back unchanged.
    """
    for rewrite in _DATETIME_REWRITE_PASSES:
        rewrite(statement)
    return statement


def _rewrite_date_function_results(tree: exp.Expr) -> None:
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
    for node in list(tree.find_all(*_DATE_RETURNING_FUNCTIONS)):
        parent = node.parent
        if isinstance(parent, exp.Cast):
            target = parent.to
            if target is not None and target.is_type(exp.DataType.Type.DATE):
                continue
        replacement = exp.Cast(this=node.copy(), to=exp.DataType.build("DATE"))
        node.replace(replacement)


def _rewrite_timestamp_micros_millis(tree: exp.Expr) -> None:
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
        expression=_interval_value(operand),
        unit=exp.Var(this=unit),
    )


def _interval_value(operand: exp.Expression) -> exp.Expression:
    """Return *operand* in SQLGlot's canonical ``INTERVAL`` value form.

    SQLGlot's parser stores a numeric interval value as a string literal
    (``INTERVAL 5 DAY`` parses as ``INTERVAL '5' DAY``), and DuckDB's
    grammar accepts an unquoted interval value only within the 32-bit
    integer range — ``INTERVAL 1705320000000000 MICROSECOND`` is a
    syntax error. A constructed interval therefore takes the same form.
    """
    if operand.is_number:
        return exp.Literal.string(operand.to_py())
    return operand.copy()


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


def _rewrite_last_day_week(tree: exp.Expr) -> None:
    """Replace every ``LastDay(x, WEEK)`` with the BigQuery semantic."""
    for node in list(tree.find_all(exp.LastDay)):
        unit = node.args.get("unit")
        if unit is None or _name(unit).upper() != "WEEK":
            continue
        operand = node.this
        if operand is None:
            continue
        replacement = _build_week_saturday(operand)
        node.replace(replacement)


def _build_week_saturday(operand: exp.Expression) -> exp.Expression:
    """Construct ``CAST(DATE_ADD(operand, INTERVAL (7 - DAYOFWEEK(operand)) DAY) AS DATE)``.

    BigQuery's ``DAYOFWEEK`` is 1 (Sunday) through 7 (Saturday); the
    offset to Saturday is ``7 - DAYOFWEEK``. For a Saturday input
    (DAYOFWEEK = 7) the offset is 0; for a Sunday input it is 6.

    The offset is passed as the plain ``Sub`` expression — SQLGlot's
    ``DateAdd`` builds the surrounding ``INTERVAL … DAY`` from the
    ``unit`` arg.

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


def _rewrite_date_trunc(tree: exp.Expr) -> None:
    """Replace date-granularity ``DATE_TRUNC`` calls with ``BQEMU_DATE_TRUNC``.

    Covers ``DAY`` / ``WEEK`` / ``WEEK(<WEEKDAY>)`` / ``ISOWEEK`` /
    ``MONTH`` / ``QUARTER`` / ``YEAR`` / ``ISOYEAR`` without a
    time-zone argument; time granularities (``HOUR`` and finer) and the
    time-zone form are left to the SQLGlot transpile. Nodes are visited
    deepest-first so a ``DATE_TRUNC`` nested in another's operand is
    rewritten in place before the enclosing call copies that operand.
    """
    for node in reversed(list(tree.find_all(exp.DateTrunc))):
        if node.args.get("zone") is not None or node.this is None:
            continue
        granularity = _date_trunc_granularity(node.args.get("unit"))
        if granularity is None:
            continue
        replacement = exp.Anonymous(
            this=DATE_TRUNC_FUNCTION,
            expressions=[node.this.copy(), exp.Literal.string(granularity)],
        )
        node.replace(replacement)


def _date_trunc_granularity(unit: exp.Expression | None) -> str | None:
    """Return the handoff granularity for a ``DATE_TRUNC`` unit, or ``None`` to keep the call.

    ``WEEK(<WEEKDAY>)`` parses to :class:`exp.WeekStart` carrying the
    weekday name; every other unit parses to a plain unit node. Time
    granularities transpile correctly and yield ``None``, as does an
    unrecognised weekday, which is left for the downstream parser to
    reject.
    """
    if unit is None:
        return None
    if isinstance(unit, exp.WeekStart):
        granularity = f"WEEK({unit.name.upper()})"
    else:
        name = _name(unit).upper()
        if name in DATE_TRUNC_CALENDAR_UNITS:
            return name
        granularity = _IMPLICIT_WEEK_GRANULARITY.get(name, "")
    return granularity if granularity in DATE_TRUNC_WEEK_START else None


def _name(node: exp.Expression) -> str:
    """Return *node*'s text content (handles ``Var`` / ``Literal`` / ``Identifier``)."""
    if isinstance(node, exp.Literal):
        return str(node.this)
    return node.name or str(node.this or "")


#: The passes :func:`rewrite_datetime_helpers` applies, in order. Each
#: targets a distinct BigQuery node type, so the order does not affect
#: the outcome.
_DATETIME_REWRITE_PASSES: tuple[Callable[[exp.Expr], None], ...] = (
    _rewrite_last_day_week,
    _rewrite_date_trunc,
    _rewrite_date_function_results,
    _rewrite_timestamp_micros_millis,
)


__all__ = [
    "DATE_TRUNC_CALENDAR_UNITS",
    "DATE_TRUNC_FUNCTION",
    "DATE_TRUNC_WEEK_START",
    "rewrite_datetime_helpers",
]
