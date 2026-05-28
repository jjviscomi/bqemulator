"""Translation rules that bridge BigQuery / DuckDB date-time + string semantics.

ADR 0023 §1.I includes a cluster of weekday / week-numbering and
string-formatting divergences that the rules below close at the
post-translate stage:

* BigQuery's ``DAYOFWEEK`` is 1-indexed (Sun = 1); DuckDB's is
  0-indexed (Sun = 0). :class:`ExtractDayofweekRule` adds 1.
* BigQuery's ``EXTRACT(WEEK FROM …)`` is the Sunday-start Gregorian
  week (0-53); DuckDB's ``WEEK`` is ISO 8601.
  :class:`ExtractWeekSundayStartRule` rewrites to a closed-form
  Sunday-start computation.
* DuckDB rejects ``EXTRACT(DATE FROM ts)``; BigQuery defines it as a
  UTC-truncating cast. :class:`ExtractDateFromTimestampRule` emits
  ``CAST(ts AS DATE)``.
* BigQuery's ``FORMAT(fmt, args…)`` implements printf-style format
  specifiers (``%d``, ``%s``, ``%05d``, ``%.3f``, ``%x``, ``%-10s``).
  DuckDB's ``FORMAT(fmt, …)`` is Python ``str.format``-style — it
  leaves ``%`` specifiers untouched. :class:`FormatPrintfRule` routes
  the call through DuckDB's ``printf`` which implements the printf
  family natively.
* BigQuery's ``FORMAT_TIME(fmt, t)`` formats a ``TIME`` value; DuckDB's
  ``STRFTIME`` does not accept ``TIME``. :class:`FormatTimeRule`
  combines the TIME with a fixed date (``1970-01-01``) so the call
  reaches ``STRFTIME`` as a ``TIMESTAMP``, and translates BigQuery's
  ``%E#S`` fractional-second extension to DuckDB's ``%S.%g`` /
  ``%S.%f``.
* BigQuery's ``PARSE_TIME(fmt, value)`` returns ``TIME``; DuckDB has
  no ``parse_time`` function. :class:`ParseTimeRule` emits
  ``CAST(strptime(value, fmt) AS TIME)``.
* BigQuery's ``PARSE_DATETIME(fmt, value)`` returns ``DATETIME``;
  DuckDB has no ``parse_datetime`` function. :class:`ParseDatetimeRule`
  emits ``strptime(value, fmt)`` whose naive-TIMESTAMP return lands on
  the wire as ``DATETIME``.
* BigQuery's ``PARSE_TIMESTAMP(fmt, value)`` returns a UTC instant;
  DuckDB's ``strptime`` returns a naive ``TIMESTAMP``.
  :class:`ParseTimestampUtcRule` wraps the call in ``timezone('UTC',
  …)`` so the result lands on the wire as ``TIMESTAMP``.
* BigQuery's ``TIME(timestamp_expr)`` extracts the UTC time component;
  DuckDB rejects ``CAST(TIMESTAMPTZ AS TIME)``.
  :class:`TimeFromTimestamptzRule` strips the timezone first
  (``timezone('UTC', ts)``) so the cast lands on a naive timestamp.
* BigQuery's ``TIME_TRUNC(time, unit)`` truncates a ``TIME``; DuckDB
  has no ``time_trunc`` function. :class:`TimeTruncRule` rewrites to
  ``CAST(DATE_TRUNC(unit, DATE '1970-01-01' + time) AS TIME)``.

DATE arithmetic and ``TIMESTAMP_MICROS`` / ``TIMESTAMP_MILLIS``
wire-format fixes happen at the *pre-translate* stage instead — see
:mod:`bqemulator.sql.rewriter.datetime_helpers`. Both forms produce
identical DuckDB AST shapes after the SQLGlot transpile, so the
distinction (function-call vs operator; int → ts vs ts → int) must be
captured while the AST is still in BigQuery shape.
"""

from __future__ import annotations

import re

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule


def _extract_specifier(node: exp.Expression) -> str:
    """Return the upper-cased name of an ``EXTRACT`` specifier."""
    specifier = node.this
    if isinstance(specifier, exp.Literal):
        return str(specifier.this).upper()
    if specifier is None:
        return ""
    name = specifier.name or str(getattr(specifier, "this", "") or "")
    return name.upper()


@register
class ExtractDateFromTimestampRule(TranslationRule):
    """``EXTRACT(DATE FROM ts)`` → ``CAST(ts AS DATE)``.

    DuckDB rejects ``DATE`` as an EXTRACT specifier ("Conversion Error:
    extract specifier 'DATE' not recognized"). BigQuery's contract is a
    simple TIMESTAMP-to-DATE cast in UTC, which is what ``CAST AS DATE``
    does for both TIMESTAMP (TIMESTAMPTZ) and DATETIME operands.
    """

    name = "EXTRACT_DATE_FROM_TS"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``Extract`` calls whose specifier is ``DATE``."""
        if not isinstance(node, exp.Extract):
            return False
        return _extract_specifier(node) == "DATE"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CAST(operand AS DATE)``."""
        operand = node.expression
        if operand is None:
            return node
        return exp.Cast(this=operand.copy(), to=exp.DataType.build("DATE"))


@register
class ExtractDayofweekRule(TranslationRule):
    """``EXTRACT(DAYOFWEEK FROM x)`` → ``EXTRACT(DAYOFWEEK FROM x) + 1``.

    BigQuery's ``DAYOFWEEK`` returns 1 for Sunday through 7 for
    Saturday. DuckDB's returns 0 for Sunday through 6 for Saturday.
    Adding 1 to the DuckDB value normalises the two conventions
    without depending on the operand's underlying type.
    """

    name = "EXTRACT_DAYOFWEEK"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``Extract`` calls whose specifier is ``DAYOFWEEK``."""
        if not isinstance(node, exp.Extract):
            return False
        return _extract_specifier(node) == "DAYOFWEEK"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``(Extract(...) + 1)`` with surrounding parens.

        We construct a fresh ``Extract`` node so the post-order walker
        does not re-visit the replacement (which would loop). The
        ``Paren`` wrap preserves precedence inside larger expressions
        like ``7 - EXTRACT(DAYOFWEEK FROM x)`` — without it the
        serializer emits ``7 - EXTRACT(...) + 1`` which DuckDB evaluates
        left-to-right and gives the wrong answer.
        """
        operand = node.expression
        if operand is None:
            return node
        fresh = exp.Extract(this=exp.Var(this="DAYOFWEEK"), expression=operand.copy())
        return exp.Paren(this=exp.Add(this=fresh, expression=exp.Literal.number(1)))


@register
class ExtractWeekSundayStartRule(TranslationRule):
    """``EXTRACT(WEEK FROM x)`` → Sunday-start week number.

    BigQuery's ``EXTRACT(WEEK FROM …)`` is the Sunday-start Gregorian
    week (0-53; the first Sunday of the year starts week 1; the days
    before it are week 0). DuckDB's ``EXTRACT(WEEK FROM …)`` is the
    ISO 8601 week (Monday-start; week 1 contains the first Thursday).

    The closed-form rewrite uses DuckDB's ``DOY`` and the day-of-week
    of January 1 (0 for Sunday, 6 for Saturday):

        week_bq = (DOY(x) - 1 + DAYOFWEEK(date_trunc('year', x))) // 7

    Trace for 2024-03-15 (a Friday): DOY = 75, Jan 1 = Monday so
    DAYOFWEEK = 1, ``(75 - 1 + 1) // 7 = 10`` ✓ — matches BigQuery's
    recorded baseline.

    The ``ExtractIsoweekRule`` runs in the same post-order pass and
    rewrites BQ's ``EXTRACT(ISOWEEK FROM x)`` to a *new* Extract node
    whose specifier is ``WEEK``. That new node is not in the walk
    snapshot, so this rule never touches it — preserving ISO semantics
    for the ISOWEEK call site.
    """

    name = "EXTRACT_WEEK_SUNDAY_START"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``Extract`` calls whose specifier is ``WEEK``."""
        if not isinstance(node, exp.Extract):
            return False
        return _extract_specifier(node) == "WEEK"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit the closed-form Sunday-start week computation."""
        operand = node.expression
        if operand is None:
            return node
        doy = exp.Extract(this=exp.Var(this="DOY"), expression=operand.copy())
        # ``DATE_TRUNC('year', x)`` returns TIMESTAMP in DuckDB; the
        # outer EXTRACT(DAYOFWEEK …) accepts TIMESTAMP just fine. We
        # build the call as ``Anonymous`` rather than ``exp.DateTrunc``
        # because the latter's constructor is untyped under SQLGlot's
        # current stubs.
        year_start: exp.Expression = exp.Anonymous(
            this="DATE_TRUNC",
            expressions=[exp.Literal.string("year"), operand.copy()],
        )
        j1_dow = exp.Extract(this=exp.Var(this="DAYOFWEEK"), expression=year_start)
        # (DOY - 1) + j1_dow — left-associative, matches the formula.
        doy_minus_one = exp.Sub(this=doy, expression=exp.Literal.number(1))
        # The serializer doesn't add parens around the numerator of
        # ``IntDiv`` even though ``//`` binds tighter than ``+`` / ``-``
        # in DuckDB — wrap explicitly so the formula evaluates as
        # ``(((DOY - 1) + j1_dow) // 7)`` not
        # ``DOY - 1 + (j1_dow // 7)``.
        numerator: exp.Expression = exp.Paren(
            this=exp.Add(this=doy_minus_one, expression=j1_dow),
        )
        # DuckDB's ``//`` is integer division (floor for non-negative).
        # All operands here are ≥ 0 so floor is safe.
        return exp.IntDiv(this=numerator, expression=exp.Literal.number(7))


@register
class ConcatStringTypeRule(TranslationRule):
    """``a || b`` → ``CAST(a || b AS VARCHAR)``.

    BigQuery's ``CONCAT(x, y)`` (and the ``||`` operator) returns
    ``STRING``. SQLGlot transpiles both to DuckDB's ``a || b``. DuckDB
    correctly returns ``VARCHAR`` when both operands are typed strings
    — but when one operand is ``CAST(NULL AS VARCHAR)`` (which BigQuery
    accepts to mean "a NULL STRING"), DuckDB collapses the expression
    to a typed-NULL whose Arrow cursor type lands on ``INTEGER`` (the
    default for an all-NULL projection). The wire-format renderer then
    surfaces the column as ``INTEGER``.

    Wrapping the ``DPipe`` in ``CAST(... AS VARCHAR)`` forces the
    column type to ``VARCHAR`` regardless of the operand types. The
    ``CAST`` is a no-op for actual string results.
    """

    name = "CONCAT_STRING_TYPE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's ``DPipe`` (``||``) node."""
        if not isinstance(node, exp.DPipe):
            return False
        # If the immediate parent is already a CAST to VARCHAR/TEXT,
        # the outer wrap is in place — skip to avoid double-wrapping.
        parent = node.parent
        if isinstance(parent, exp.Cast):
            target = parent.to
            if target is not None and (
                target.is_type(exp.DataType.Type.VARCHAR) or target.is_type(exp.DataType.Type.TEXT)
            ):
                return False
        return True

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the concatenation in ``CAST(... AS VARCHAR)``."""
        return exp.Cast(this=node.copy(), to=exp.DataType.build("VARCHAR"))


@register
class ApproxQuantilesDiscreteRule(TranslationRule):
    """``APPROX_QUANTILE(x, [q...])`` → ``QUANTILE_DISC(x, [q...])``.

    DuckDB's ``approx_quantile`` (the t-digest implementation behind
    BigQuery's ``APPROX_QUANTILES`` after the SQLGlot transpile)
    interpolates between samples and lands on values BigQuery's exact
    discrete quantile algorithm does not return — e.g. for the 0-9
    integer set ``approx_quantile`` returns ``[1, 3, 6, 8, 10]`` where
    BigQuery returns ``[1, 3, 5, 8, 10]``. The conformance corpus
    captures BigQuery's exact output, so we route the call through
    DuckDB's ``quantile_disc`` aggregate which returns a discrete
    sample (matching BigQuery's documented behaviour exactly).
    """

    name = "APPROX_QUANTILES_DISCRETE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``ApproxQuantile`` AST node."""
        return isinstance(node, exp.ApproxQuantile)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``quantile_disc(x, quantile)``."""
        operand = node.this
        quantile = node.args.get("quantile")
        if operand is None or quantile is None:
            return node
        return exp.Anonymous(
            this="quantile_disc",
            expressions=[operand.copy(), quantile.copy()],
        )


@register
class ApproxCountDistinctExactRule(TranslationRule):
    """``APPROX_COUNT_DISTINCT(x)`` → ``COUNT(DISTINCT x)``.

    DuckDB's ``approx_count_distinct`` returns 11 for a 10-distinct
    integer set — its HyperLogLog stand-in lacks the small-cardinality
    fixup BigQuery applies. The exact ``COUNT(DISTINCT x)`` matches
    BigQuery for every size of input (and matches BigQuery's
    ``APPROX_COUNT_DISTINCT`` exactly for the small inputs the
    conformance corpus exercises). For very large datasets the
    emulator runs locally so the performance gap of going exact is
    acceptable.
    """

    name = "APPROX_COUNT_DISTINCT_EXACT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's ``ApproxDistinct`` typed node."""
        return isinstance(node, exp.ApproxDistinct)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``COUNT(DISTINCT x)`` preserving the operand."""
        operand = node.this
        if operand is None:
            return node
        return exp.Count(this=exp.Distinct(expressions=[operand.copy()]))


@register
class FormatPrintfRule(TranslationRule):
    """``FORMAT(fmt, args…)`` → ``printf(fmt, args…)``.

    BigQuery's ``FORMAT`` follows the C printf family — ``%d``, ``%s``,
    ``%.3f``, ``%05d``, ``%-10s``, ``%x`` etc. DuckDB ships two
    different formatters: ``format()`` uses Python ``str.format`` style
    (``{}`` placeholders) and silently ignores ``%`` specifiers, while
    ``printf()`` implements the C printf grammar. Routing the call
    through ``printf`` matches BigQuery exactly without us reimplementing
    the format-string parser.
    """

    name = "FORMAT_PRINTF"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``Format`` node from BigQuery's FORMAT."""
        return isinstance(node, exp.Format)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``printf(fmt, args…)``."""
        fmt = node.this
        if fmt is None:
            return node
        args = [fmt.copy(), *(arg.copy() for arg in node.expressions)]
        return exp.Anonymous(this="printf", expressions=args)


@register
class ParseTimeRule(TranslationRule):
    """``PARSE_TIME(value, fmt)`` → ``CAST(strptime(value, fmt) AS TIME)``.

    DuckDB does not implement ``parse_time``; ``strptime`` returns a
    ``TIMESTAMP`` which the explicit cast narrows to ``TIME``. The
    BigQuery arg order is ``(format, value)`` but SQLGlot swaps to the
    DuckDB-native ``(value, format)`` during the transpile — the
    ``ParseTime`` AST node at this point already carries
    ``this=value``, ``format=format``.
    """

    name = "PARSE_TIME"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed ``ParseTime`` AST node."""
        return isinstance(node, exp.ParseTime)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CAST(strptime(value, fmt) AS TIME)``."""
        value = node.this
        fmt = node.args.get("format")
        if value is None or fmt is None:
            return node
        strptime_call = exp.Anonymous(
            this="strptime",
            expressions=[value.copy(), fmt.copy()],
        )
        return exp.Cast(this=strptime_call, to=exp.DataType.build("TIME"))


@register
class JsonTypeLowerRule(TranslationRule):
    """``JSON_TYPE(x)`` → ``LOWER(JSON_TYPE(x))``.

    BigQuery returns the JSON value's type in lowercase (``object``,
    ``array``, ``string``, ``number``, ``boolean``, ``null``). DuckDB
    returns it in uppercase (``OBJECT``, ``ARRAY``, …). Wrapping in
    ``LOWER`` is sufficient since the casing is the only divergence.
    """

    name = "JSON_TYPE_LOWER"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``JSONType`` node."""
        if not isinstance(node, exp.JSONType):
            return False
        # An outer ``LOWER(JSON_TYPE(…))`` already settles the casing.
        return not isinstance(node.parent, exp.Lower)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the ``JSON_TYPE`` call in ``LOWER(...)``."""
        return exp.Lower(this=node.copy())


@register
class ParseTimestampUtcRule(TranslationRule):
    """``STRPTIME(value, fmt)`` → ``timezone('UTC', STRPTIME(value, fmt))``.

    BigQuery's ``PARSE_TIMESTAMP(fmt, value)`` returns a UTC instant
    (``TIMESTAMP`` in BigQuery wire-format terms); SQLGlot transpiles it
    to DuckDB's ``strptime(value, fmt)``, which returns a *naive*
    ``TIMESTAMP``. The wire-format renderer surfaces a naive timestamp
    as ``DATETIME``. Wrapping in ``timezone('UTC', …)`` flips the
    column to ``TIMESTAMPTZ`` so the renderer emits the
    microseconds-since-epoch integer the BigQuery Python client parses
    back into a UTC ``datetime``.
    """

    name = "PARSE_TIMESTAMP_UTC"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's ``StrToTime`` node (DuckDB ``strptime``)."""
        if not isinstance(node, exp.StrToTime):
            return False
        # Don't double-wrap when an outer ``timezone(...)`` already
        # settles the tz.
        parent = node.parent
        return not (isinstance(parent, exp.Anonymous) and str(parent.this).upper() == "TIMEZONE")

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the ``strptime`` call in ``timezone('UTC', …)``."""
        return exp.Anonymous(
            this="timezone",
            expressions=[exp.Literal.string("UTC"), node.copy()],
        )


_BQ_FRACTIONAL_SECOND = re.compile(r"%E(\d+|\*)S")
#: Threshold at and below which DuckDB's ``%S.%g`` (millisecond, 3
#: fractional digits) is the closest match for BigQuery's ``%E#S``;
#: above this DuckDB has to fall back to ``%S.%f`` (microsecond, 6).
_BQ_MILLISECOND_MAX_DIGITS = 3


def _translate_bq_strftime_format(fmt: str) -> str:
    """Translate BigQuery ``%E#S`` fractional-second tokens to DuckDB.

    BigQuery's strftime grammar adds ``%E0S``..``%E9S`` and ``%E*S`` for
    formatting fractional seconds with a fixed or maximal number of
    digits. DuckDB's strftime does not recognise the ``%E`` family; the
    closest equivalents are ``%S`` (whole seconds), ``%S.%g``
    (milliseconds, 3 digits), and ``%S.%f`` (microseconds, 6 digits).
    The mapping ``digits ≤ 0 → %S``, ``1 ≤ digits ≤ 3 → %S.%g``,
    ``digits ≥ 4 → %S.%f`` covers the cases the conformance corpus
    exercises and keeps the trailing-digit precision tighter than BQ's
    when DuckDB has no narrower option.
    """

    def repl(match: re.Match[str]) -> str:
        digits = match.group(1)
        if digits == "*":
            return "%S.%f"
        n = int(digits)
        if n <= 0:
            return "%S"
        if n <= _BQ_MILLISECOND_MAX_DIGITS:
            return "%S.%g"
        return "%S.%f"

    return _BQ_FRACTIONAL_SECOND.sub(repl, fmt)


@register
class FormatTimeRule(TranslationRule):
    """``FORMAT_TIME(fmt, t)`` → ``STRFTIME(DATE '1970-01-01' + t, fmt)``.

    DuckDB's ``STRFTIME`` does not accept the ``TIME`` type. BigQuery's
    ``FORMAT_TIME`` formats a ``TIME`` value (hours / minutes / seconds
    plus optional fractional seconds) into a ``STRING``. We bridge by
    combining the ``TIME`` with a fixed date (``1970-01-01``) which
    DuckDB happily promotes to a naive ``TIMESTAMP`` that ``STRFTIME``
    can format. The format string is passed through
    :func:`_translate_bq_strftime_format` so BigQuery's ``%E#S``
    fractional-second extension maps to a DuckDB-recognised specifier.

    The post-translate AST shape is ``TimeToStr(this=Cast(... AS TIME),
    format=fmt)`` — the inner ``Cast(... AS TIME)`` is the
    distinguishing feature versus ``FORMAT_DATETIME`` (``CAST(...
    AS TIMESTAMP)``) and ``FORMAT_TIMESTAMP`` (``CAST(... AS TIMESTAMPTZ)``)
    which already work in DuckDB.
    """

    name = "FORMAT_TIME"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``TimeToStr`` whose operand is a TIME-typed cast."""
        if not isinstance(node, exp.TimeToStr):
            return False
        inner = node.this
        if isinstance(inner, exp.Cast):
            target = inner.to
            return target is not None and target.is_type(exp.DataType.Type.TIME)
        return False

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``STRFTIME(CAST('1970-01-01' AS DATE) + t, fmt')``."""
        time_expr = node.this
        fmt_node = node.args.get("format")
        if time_expr is None or fmt_node is None:
            return node
        translated_fmt = _translate_bq_strftime_format(str(fmt_node.this))
        date_plus_time = exp.Paren(
            this=exp.Add(
                this=exp.Cast(
                    this=exp.Literal.string("1970-01-01"),
                    to=exp.DataType.build("DATE"),
                ),
                expression=time_expr.copy(),
            ),
        )
        return exp.Anonymous(
            this="STRFTIME",
            expressions=[date_plus_time, exp.Literal.string(translated_fmt)],
        )


def _split_format_on_year(fmt: str) -> list[str]:
    """Split a strftime format string on the ``%Y`` directive.

    Returns the literal / non-``%Y`` runs between ``%Y`` occurrences: *N*
    occurrences of ``%Y`` yield *N + 1* parts (any of which may be
    empty). ``%%`` is a literal percent, so ``%%Y`` is a literal ``%``
    followed by a literal ``Y`` and is **not** a split point. Every other
    ``%`` directive (``%m``, ``%d``, …) is carried through verbatim as a
    two-character token so DuckDB's ``STRFTIME`` still formats it.

    Used by :class:`FormatDateYearPadRule` to splice an unpadded year
    between the surrounding format runs.
    """
    parts: list[str] = []
    run: list[str] = []
    i = 0
    n = len(fmt)
    while i < n:
        if fmt[i] == "%" and i + 1 < n:
            nxt = fmt[i + 1]
            if nxt == "Y":
                parts.append("".join(run))
                run = []
                i += 2
                continue
            # ``%%`` (literal percent) or any other directive — keep the
            # two-character token together so it reaches STRFTIME intact.
            run.append(fmt[i : i + 2])
            i += 2
            continue
        run.append(fmt[i])
        i += 1
    parts.append("".join(run))
    return parts


#: A format string with no real ``%Y`` split point tokenizes to a single
#: part; a real ``%Y`` yields at least two. Below this the rule is a no-op
#: (the ``"%Y"`` substring that triggered :meth:`applies_to` was an
#: escaped ``%%Y``).
_MIN_YEAR_SPLIT_PARTS = 2


@register
class FormatDateYearPadRule(TranslationRule):
    """``FORMAT_DATE('…%Y…', d)`` → unpadded-year rewrite (years < 1000).

    DuckDB's ``STRFTIME`` zero-pads ``%Y`` to four digits per POSIX
    ``strftime(3)`` (``DATE '0001-01-01'`` → ``'0001-01-01'``).
    BigQuery's ``FORMAT_DATE`` emits the minimum-width year
    (``'1-01-01'``). The two agree for years ≥ 1000, so the divergence
    only surfaces for years 1-999; DuckDB exposes no no-pad flag
    (``%-Y`` errors).

    Rather than reimplement every conversion specifier in Python, this
    rule keeps DuckDB's ``STRFTIME`` as the engine for every specifier
    *except* ``%Y`` and substitutes only the year with
    ``CAST(EXTRACT(YEAR FROM d) AS VARCHAR)`` (no zero-pad), splicing the
    surrounding format runs back with ``||``::

        FORMAT_DATE('%Y-%m-%d', d)
            → CAST(EXTRACT(YEAR FROM d) AS VARCHAR) || STRFTIME(d, '-%m-%d')

    ``NULL`` propagates through ``||`` (a NULL date yields NULL), matching
    BigQuery. The whole concatenation is wrapped in ``CAST(… AS VARCHAR)``
    to pin the column to STRING: an all-NULL ``||`` chain would otherwise
    surface as INTEGER on the wire — the same hazard
    :class:`ConcatStringTypeRule` guards against, which cannot fire on
    our freshly-emitted node (the rule walker snapshots the tree before
    rewriting). Empty format runs are dropped — DuckDB's ``STRFTIME``
    rejects an empty format string.

    Scope is ``FORMAT_DATE`` only: SQLGlot wraps every FORMAT_DATE
    argument in ``CAST(… AS DATE)`` (for literals, columns and function
    results alike), which distinguishes the call from ``FORMAT_DATETIME``
    / ``FORMAT_TIMESTAMP`` (``CAST(… AS TIMESTAMP)``). A zoned ``%Y`` on
    FORMAT_TIMESTAMP is a separate concern and is not in the conformance
    corpus.

    Fires only when the format is a string literal containing ``%Y``; a
    dynamic (non-literal) format is left on DuckDB's native path.
    """

    name = "FORMAT_DATE_YEAR_PAD"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``STRFTIME(CAST(… AS DATE), '<string literal with %Y>')``."""
        if not isinstance(node, exp.TimeToStr):
            return False
        fmt = node.args.get("format")
        if not isinstance(fmt, exp.Literal) or not fmt.is_string:
            return False
        if "%Y" not in str(fmt.this):
            return False
        inner = node.this
        if not isinstance(inner, exp.Cast):
            return False
        target = inner.to
        return target is not None and target.is_type(exp.DataType.Type.DATE)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``(… STRFTIME(d, run) || CAST(EXTRACT(YEAR FROM d) AS VARCHAR) …)``."""
        date_expr = node.this
        fmt_node = node.args.get("format")
        if date_expr is None or fmt_node is None:
            return node
        parts = _split_format_on_year(str(fmt_node.this))
        if len(parts) < _MIN_YEAR_SPLIT_PARTS:
            # No real ``%Y`` split point — the match was an escaped
            # ``%%Y``. Leave DuckDB's native STRFTIME untouched.
            return node
        terms: list[exp.Expression] = []
        for index, part in enumerate(parts):
            if part:
                terms.append(
                    exp.Anonymous(
                        this="STRFTIME",
                        expressions=[date_expr.copy(), exp.Literal.string(part)],
                    ),
                )
            if index < len(parts) - 1:
                terms.append(
                    exp.Cast(
                        this=exp.Extract(
                            this=exp.Var(this="YEAR"),
                            expression=date_expr.copy(),
                        ),
                        to=exp.DataType.build("VARCHAR"),
                    ),
                )
        if not terms:
            return node
        concat = terms[0]
        for term in terms[1:]:
            concat = exp.DPipe(this=concat, expression=term)
        # Wrap in ``CAST(… AS VARCHAR)`` so the column type stays STRING
        # even when every operand is NULL (an all-NULL ``||`` chain
        # otherwise lands on INTEGER on the wire). ``CAST`` also supplies
        # the grouping the surrounding expression needs.
        return exp.Cast(this=concat, to=exp.DataType.build("VARCHAR"))


@register
class ParseDatetimeRule(TranslationRule):
    """``PARSE_DATETIME(fmt, value)`` → ``strptime(value, fmt)``.

    BigQuery's ``PARSE_DATETIME`` returns a naive ``DATETIME``. DuckDB
    has no ``parse_datetime`` function, but ``strptime(value, fmt)``
    returns a naive ``TIMESTAMP`` whose wire-format representation is
    ``DATETIME``. The SQLGlot AST node is ``ParseDatetime`` (the
    BigQuery-specific typed node); arguments are already in
    ``(value, format)`` order on the AST.
    """

    name = "PARSE_DATETIME"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the BigQuery ``ParseDatetime`` AST node."""
        return type(node).__name__ == "ParseDatetime"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``strptime(value, fmt)``."""
        value = node.this
        fmt = node.args.get("format")
        if value is None or fmt is None:
            return node
        return exp.Anonymous(
            this="strptime",
            expressions=[value.copy(), fmt.copy()],
        )


@register
class TimeFromTimestamptzRule(TranslationRule):
    """``TIME(timestamp)`` → ``CAST(timezone('UTC', timestamp) AS TIME)``.

    BigQuery's ``TIME(timestamp_expr)`` returns the UTC time-of-day
    component of a ``TIMESTAMP``. SQLGlot transpiles this to
    ``CAST(<ts> AS TIME)``, but DuckDB rejects ``CAST(TIMESTAMPTZ AS
    TIME)`` outright ("Unimplemented type for cast"). Wrapping the
    operand in ``timezone('UTC', …)`` produces a naive ``TIMESTAMP``
    whose components are the UTC clock time; that naive timestamp casts
    cleanly to ``TIME`` and preserves BigQuery's UTC contract regardless
    of the session timezone.

    The post-translate AST shape is ``Cast(this=<TIMESTAMPTZ-typed>,
    to=TIME)``. We match an outer ``Cast`` whose target is ``TIME`` and
    whose operand is itself a ``Cast`` to ``TIMESTAMPTZ`` (or otherwise
    a TIMESTAMPTZ-typed expression). Bare ``TIME '...'`` literals are
    safe because their AST is a single ``Cast(literal AS TIME)`` — the
    operand is a string literal, not a TIMESTAMPTZ-typed expression.
    """

    name = "TIME_FROM_TIMESTAMPTZ"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match outer ``Cast`` to ``TIME`` wrapping a TIMESTAMPTZ expression."""
        if not isinstance(node, exp.Cast):
            return False
        target = node.to
        if target is None or not target.is_type(exp.DataType.Type.TIME):
            return False
        # Avoid re-triggering on the rule's own ``CAST(timezone(...) AS TIME)`` output.
        inner = node.this
        if isinstance(inner, exp.Anonymous) and str(inner.this).upper() == "TIMEZONE":
            return False
        if isinstance(inner, exp.Cast):
            inner_target = inner.to
            return inner_target is not None and inner_target.is_type(exp.DataType.Type.TIMESTAMPTZ)
        return False

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CAST(timezone('UTC', ts) AS TIME)``."""
        inner = node.this
        if inner is None:
            return node
        normalised = exp.Anonymous(
            this="timezone",
            expressions=[exp.Literal.string("UTC"), inner.copy()],
        )
        return exp.Cast(this=normalised, to=exp.DataType.build("TIME"))


@register
class TimeTruncRule(TranslationRule):
    """``TIME_TRUNC(t, unit)`` → ``CAST(DATE_TRUNC(unit, DATE '1970-01-01' + t) AS TIME)``.

    DuckDB has no ``time_trunc`` function. ``DATE_TRUNC`` accepts
    ``TIMESTAMP`` operands; combining the ``TIME`` with a fixed date
    (``1970-01-01``) materialises a ``TIMESTAMP`` we can truncate, and
    casting back to ``TIME`` returns the wire-format shape the
    BigQuery client expects.

    The unit argument is preserved verbatim (``HOUR``, ``MINUTE``,
    ``SECOND``, ``MILLISECOND``, ``MICROSECOND``) — DuckDB's
    ``DATE_TRUNC`` accepts the same set as a string literal.
    """

    name = "TIME_TRUNC"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed ``TimeTrunc`` AST node."""
        return isinstance(node, exp.TimeTrunc)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CAST(DATE_TRUNC('unit', DATE '1970-01-01' + t) AS TIME)``."""
        time_expr = node.this
        unit = node.args.get("unit")
        if time_expr is None or unit is None:
            return node
        unit_name = unit.name or str(getattr(unit, "this", "") or "")
        date_plus_time = exp.Paren(
            this=exp.Add(
                this=exp.Cast(
                    this=exp.Literal.string("1970-01-01"),
                    to=exp.DataType.build("DATE"),
                ),
                expression=time_expr.copy(),
            ),
        )
        truncated = exp.Anonymous(
            this="DATE_TRUNC",
            expressions=[
                exp.Literal.string(unit_name.lower() or "second"),
                date_plus_time,
            ],
        )
        return exp.Cast(this=truncated, to=exp.DataType.build("TIME"))


_NUMERIC_OFFSET_RE = re.compile(r"^[+-]\d{2}:\d{2}$")


@register
class AtTimeZoneNumericOffsetRule(TranslationRule):
    """``ts AT TIME ZONE '+HH:MM' / '-HH:MM'`` → interval arithmetic.

    DuckDB's ICU build does not accept numeric-offset literals such as
    ``'-04:30'`` or ``'+05:45'`` as a zone — it errors with
    ``Not implemented Error: Unknown TimeZone``. BigQuery accepts the
    numeric-offset form natively, so this rule rewrites
    ``ts AT TIME ZONE '<sign>HH:MM'`` to the algebraic equivalent
    ``(ts AT TIME ZONE 'UTC') + INTERVAL '<sign>HH:MM' HOUR TO MINUTE``.

    The input ``ts`` is a TIMESTAMPTZ (the BigQuery-emitted form after
    the SQLGlot transpile lifts the literal into a ``CAST AS TIMESTAMPTZ``).
    ``AT TIME ZONE 'UTC'`` returns a naive TIMESTAMP showing the UTC
    components; adding the offset shifts the wall-clock to the local
    components in the offset zone — the same value BigQuery's
    ``EXTRACT(part FROM ts AT TIME ZONE '<sign>HH:MM')`` extracts.
    """

    name = "AT_TIME_ZONE_NUMERIC_OFFSET"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``AtTimeZone`` nodes whose ``zone`` is a numeric-offset literal."""
        if not isinstance(node, exp.AtTimeZone):
            return False
        zone = node.args.get("zone")
        if not isinstance(zone, exp.Literal) or not zone.is_string:
            return False
        return _NUMERIC_OFFSET_RE.match(str(zone.this)) is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``(ts AT TIME ZONE 'UTC') + INTERVAL '<sign>HH:MM' HOUR TO MINUTE``."""
        ts = node.this
        zone = node.args.get("zone")
        if ts is None or zone is None:
            return node
        offset_text = str(zone.this)
        utc_naive = exp.AtTimeZone(this=ts.copy(), zone=exp.Literal.string("UTC"))
        # DuckDB accepts ``INTERVAL '<sign>HH:MM' HOUR TO MINUTE`` syntactically,
        # but the SQLGlot ``Interval`` AST does not surface a ``HOUR TO MINUTE``
        # range in a serializer-friendly way across versions. Emit the offset
        # as separate ``HOUR`` + ``MINUTE`` intervals — algebraically identical
        # and serializer-stable.
        sign = -1 if offset_text[0] == "-" else 1
        hours = int(offset_text[1:3]) * sign
        minutes = int(offset_text[4:6]) * sign
        hour_interval = exp.Interval(
            this=exp.Literal.number(hours),
            unit=exp.Var(this="HOUR"),
        )
        minute_interval = exp.Interval(
            this=exp.Literal.number(minutes),
            unit=exp.Var(this="MINUTE"),
        )
        return exp.Paren(
            this=exp.Add(
                this=exp.Add(this=utc_naive, expression=hour_interval),
                expression=minute_interval,
            ),
        )


@register
class TimestampTruncWeekZoneSundayRule(TranslationRule):
    """``DATE_TRUNC('WEEK', ts AT TIME ZONE 'X')`` → Sunday-start truncation.

    BigQuery's ``TIMESTAMP_TRUNC(ts, WEEK [, zone])`` truncates to the
    start of the week, defaulting to Sunday. SQLGlot transpiles the
    BigQuery form to DuckDB's ``DATE_TRUNC('WEEK', X AT TIME ZONE 'Z')
    AT TIME ZONE 'Z'`` — but DuckDB's ``DATE_TRUNC('week', X)`` follows
    ISO 8601 (Monday-start). When the input lands on Sunday in the
    target zone, DuckDB returns the *next* Monday's week start, which
    is one day too late relative to BigQuery's recorded output.

    The closed-form Sunday-start week truncation rewrites the inner
    ``DATE_TRUNC('week', X)`` to ``DATE_TRUNC('day', X) - INTERVAL
    EXTRACT(DOW FROM X) DAY``. DuckDB's ``EXTRACT(DOW FROM ts)``
    returns 0 for Sunday through 6 for Saturday, so subtracting that
    many days from the day-truncated value lands on the previous
    Sunday (or the same Sunday if the input was already Sunday).

    The rule fires on the post-translate ``TimestampTrunc`` node whose
    ``unit=WEEK`` and whose ``this`` is already an :class:`exp.AtTimeZone`
    wrap (the SQLGlot transpile's standard shape for the zoned form).
    Timestamp-only ``TIMESTAMP_TRUNC(ts, WEEK)`` without a zone is
    *not* in the conformance corpus today; if it appears in a future
    fixture, a separate AST pattern (no inner ``AtTimeZone``) will be
    needed.

    BigQuery's WEEK starts on Sunday; DuckDB's ISO-week truncation
    rounds to Monday, off by one day. This rule re-pivots the
    ``TIMESTAMP_TRUNC`` to a Sunday-anchored boundary before
    reapplying the zone.
    """

    name = "TIMESTAMP_TRUNC_WEEK_ZONE_SUNDAY"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``TimestampTrunc(this=AtTimeZone, unit=WEEK)``."""
        if not isinstance(node, exp.TimestampTrunc):
            return False
        unit = node.args.get("unit")
        if unit is None:
            return False
        unit_name = unit.name or str(getattr(unit, "this", "") or "")
        if unit_name.upper() != "WEEK":
            return False
        return isinstance(node.this, exp.AtTimeZone)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit Sunday-start truncation preserving the inner ``AtTimeZone`` wrap.

        Result shape (the outer ``AT TIME ZONE 'X'`` that SQLGlot
        emitted around the original ``DATE_TRUNC`` is left intact —
        only the inner ``DATE_TRUNC`` is rewritten)::

            (DATE_TRUNC('day', ts AT TIME ZONE 'X')
             - INTERVAL EXTRACT(DOW FROM ts AT TIME ZONE 'X') DAY)
        """
        local = node.this
        if not isinstance(local, exp.AtTimeZone):
            return node
        day_trunc = exp.Anonymous(
            this="DATE_TRUNC",
            expressions=[exp.Literal.string("day"), local.copy()],
        )
        dow_extract = exp.Extract(this=exp.Var(this="DOW"), expression=local.copy())
        offset_interval = exp.Interval(this=dow_extract, unit=exp.Var(this="DAY"))
        return exp.Paren(
            this=exp.Sub(this=day_trunc, expression=offset_interval),
        )


__all__ = [
    "ApproxCountDistinctExactRule",
    "ApproxQuantilesDiscreteRule",
    "AtTimeZoneNumericOffsetRule",
    "ConcatStringTypeRule",
    "ExtractDateFromTimestampRule",
    "ExtractDayofweekRule",
    "ExtractWeekSundayStartRule",
    "FormatDateYearPadRule",
    "FormatPrintfRule",
    "FormatTimeRule",
    "JsonTypeLowerRule",
    "ParseDatetimeRule",
    "ParseTimeRule",
    "ParseTimestampUtcRule",
    "TimeFromTimestamptzRule",
    "TimeTruncRule",
    "TimestampTruncWeekZoneSundayRule",
]
