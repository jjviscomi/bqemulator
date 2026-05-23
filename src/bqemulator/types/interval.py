"""BigQuery INTERVAL literal parsing and DuckDB translation helpers.

BigQuery's compound interval literal looks like::

    INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND

That is: ``Y-M D H:M:S[.f]`` — year/month, day, hours/minutes/seconds.

DuckDB does not support the ``YEAR TO SECOND`` form natively. The SQL
rule pipeline parses the literal here and emits an equivalent expression
of the form::

    INTERVAL '1' YEAR + INTERVAL '2' MONTH + INTERVAL '3' DAY
    + INTERVAL '4' HOUR + INTERVAL '5' MINUTE + INTERVAL '6.789' SECOND

This module is the parser. It also owns the JUSTIFY helper that emits
DuckDB expressions for ``JUSTIFY_HOURS`` / ``JUSTIFY_DAYS`` /
``JUSTIFY_INTERVAL`` (DuckDB has none of these natively).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from bqemulator.domain.errors import ValidationError


@dataclass(slots=True, frozen=True)
class IntervalParts:
    """Parsed components of a BigQuery interval literal.

    All fields are signed; a single leading ``-`` on the literal applies
    to every component (BigQuery treats a compound interval as one
    signed quantity).

    Attributes:
        years: Year component (whole number).
        months: Month component (whole number).
        days: Day component (whole number).
        hours: Hour component (whole number).
        minutes: Minute component (whole number).
        seconds: Fractional seconds component as a :class:`Decimal`
            so we don't lose precision on ``.789``-style inputs.
    """

    years: int = 0
    months: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: Decimal = Decimal(0)


# Order matters: ``YEAR TO SECOND`` enables every component; smaller
# spans turn the trailing ones off.
_SPAN_FIELDS: dict[str, tuple[str, ...]] = {
    "YEAR TO SECOND": ("years", "months", "days", "hours", "minutes", "seconds"),
    "YEAR TO MONTH": ("years", "months"),
    "DAY TO HOUR": ("days", "hours"),
    "DAY TO MINUTE": ("days", "hours", "minutes"),
    "DAY TO SECOND": ("days", "hours", "minutes", "seconds"),
    "HOUR TO MINUTE": ("hours", "minutes"),
    "HOUR TO SECOND": ("hours", "minutes", "seconds"),
    "MINUTE TO SECOND": ("minutes", "seconds"),
}

# Single-unit shorthands like ``INTERVAL 1 DAY``.
_SINGLE_UNITS: dict[str, str] = {
    "YEAR": "years",
    "MONTH": "months",
    "DAY": "days",
    "HOUR": "hours",
    "MINUTE": "minutes",
    "SECOND": "seconds",
}


def parse_interval_literal(literal: str, span: str) -> IntervalParts:
    """Parse a BigQuery interval literal string and span into typed components.

    Args:
        literal: The string content between the quotes (without the
            ``'`` delimiters), e.g. ``"1-2 3 4:5:6.789"``.
        span: Either a single unit (``"DAY"``) or a compound span
            (``"YEAR TO SECOND"``), case-insensitive.

    Returns:
        An :class:`IntervalParts` with the parsed components.

    Raises:
        ValidationError: If the literal cannot be parsed against the
            requested span.
    """
    span_norm = " ".join(span.upper().split())
    raw = literal.strip()
    sign = 1
    if raw.startswith("-"):
        sign = -1
        raw = raw[1:].strip()

    if span_norm in _SINGLE_UNITS:
        return _parse_single_unit(raw, span_norm, sign)

    fields = _SPAN_FIELDS.get(span_norm)
    if fields is None:
        raise ValidationError(
            f"Unsupported INTERVAL span {span!r}; expected one of "
            f"{', '.join(sorted(_SINGLE_UNITS) + sorted(_SPAN_FIELDS))}.",
        )
    return _parse_compound(raw, fields, sign)


def _parse_single_unit(raw: str, span: str, sign: int) -> IntervalParts:
    field = _SINGLE_UNITS[span]
    if field == "seconds":
        try:
            value = Decimal(raw) * sign
        except Exception as exc:
            raise ValidationError(f"Cannot parse INTERVAL seconds {raw!r}: {exc}") from exc
        return IntervalParts(seconds=value)
    try:
        ivalue = int(raw) * sign
    except ValueError as exc:
        raise ValidationError(f"Cannot parse INTERVAL {span} {raw!r}: {exc}") from exc
    kwargs: dict[str, int | Decimal] = {field: ivalue}
    return IntervalParts(**kwargs)  # type: ignore[arg-type]


_COMPOUND_RE = re.compile(
    r"""^
    (?:(?P<ym>-?\d+(?:-\d+)?)\s+)?  # optional ``Y`` or ``Y-M`` block
    (?:(?P<d>-?\d+)\s+)?            # optional ``D`` block
    (?:(?P<hms>-?\d+:\d+(?::\d+(?:\.\d+)?)?))?  # optional ``H:M[:S[.f]]`` block
    \s*$
    """,
    re.VERBOSE,
)


def _parse_compound(raw: str, fields: tuple[str, ...], sign: int) -> IntervalParts:
    # Split into space-separated blocks then parse each per its position.
    blocks = raw.split()
    if not blocks:
        raise ValidationError(f"Empty INTERVAL literal {raw!r}.")

    parts = _consume_blocks(blocks, fields)
    parts = {key: value * sign for key, value in parts.items()}
    return IntervalParts(**parts)  # type: ignore[arg-type]


def _parse_int_token(raw: str, *, field: str) -> int:
    """Parse an INTERVAL block's int-typed token, normalising errors.

    A bare ``int(raw)`` raises ``ValueError`` on a malformed literal,
    which leaks out of the parser's documented contract — every
    other parse failure surfaces as :class:`ValidationError` with a
    pointing error message. This wrapper re-raises so callers can
    rely on the single exception type without per-call try/except
    churn.
    """
    try:
        return int(raw)
    except ValueError as exc:
        raise ValidationError(
            f"Cannot parse INTERVAL {field} {raw!r}: {exc}",
        ) from exc


def _consume_year_month_block(
    blocks: list[str],
    pos: int,
    fields: tuple[str, ...],
    out: dict[str, int | Decimal],
) -> int:
    """Consume the optional ``Y`` / ``Y-M`` block. Returns the new position.

    Three shapes:
    - ``Y-M`` when both years and months are in scope and the token
      carries the dash separator (and isn't itself a leading-dash
      negative number — that case falls through to single-unit).
    - ``Y`` when only years is in scope.
    - ``M`` when only months is in scope.
    """
    if ("years" not in fields and "months" not in fields) or pos >= len(blocks):
        return pos
    token = blocks[pos]
    if "years" in fields and "months" in fields and "-" in token and not token.startswith("-"):
        yr_s, mo_s = token.split("-", 1)
        out["years"] = _parse_int_token(yr_s, field="years")
        out["months"] = _parse_int_token(mo_s, field="months")
    elif "years" in fields:
        out["years"] = _parse_int_token(token, field="years")
    else:  # months only
        out["months"] = _parse_int_token(token, field="months")
    return pos + 1


def _consume_day_block(
    blocks: list[str],
    pos: int,
    fields: tuple[str, ...],
    out: dict[str, int | Decimal],
) -> int:
    """Consume the optional day-count block. Returns the new position.

    Returns ``pos`` unchanged when the current token looks like a
    time block (contains ``:``) — that means no day block was
    supplied and we fall through to the time-block consumer.
    """
    if "days" not in fields or pos >= len(blocks):
        return pos
    token = blocks[pos]
    if ":" in token:
        return pos
    out["days"] = _parse_int_token(token, field="days")
    return pos + 1


def _consume_time_block_segments(
    token: str,
    time_fields: tuple[str, ...],
    out: dict[str, int | Decimal],
) -> None:
    """Parse a colon-separated time block into ``out`` per ``time_fields``.

    Seconds are kept as ``Decimal`` so fractional precision is
    preserved through arithmetic; hours / minutes stay as ``int``.
    Raises ``ValidationError`` for malformed shapes (no colon, too
    many segments, unparseable seconds).
    """
    if ":" not in token:
        raise ValidationError(
            f"Expected H:M[:S[.f]] block in INTERVAL literal, got {token!r}.",
        )
    segs = token.split(":")
    if len(segs) > len(time_fields):
        raise ValidationError(
            f"Too many colon-separated parts in INTERVAL literal {token!r} "
            f"for span {' TO '.join(f.upper()[:-1] for f in time_fields)}.",
        )
    for field, raw_value in zip(time_fields, segs, strict=False):
        if field == "seconds":
            try:
                out["seconds"] = Decimal(raw_value)
            except Exception as exc:
                raise ValidationError(
                    f"Cannot parse INTERVAL seconds {raw_value!r}: {exc}",
                ) from exc
        else:
            out[field] = _parse_int_token(raw_value, field=field)


def _consume_time_block(
    blocks: list[str],
    pos: int,
    fields: tuple[str, ...],
    out: dict[str, int | Decimal],
) -> int:
    """Consume the optional time block. Returns the new position.

    Single-unit shorthand (e.g. DAY TO HOUR with no nested colon
    structure) treats the whole token as an int for the lone time
    field; multi-unit spans dispatch into
    :func:`_consume_time_block_segments`.
    """
    time_fields = tuple(f for f in ("hours", "minutes", "seconds") if f in fields)
    if not time_fields or pos >= len(blocks):
        return pos
    token = blocks[pos]
    if len(time_fields) == 1:
        out[time_fields[0]] = _parse_int_token(token, field=time_fields[0])
    else:
        _consume_time_block_segments(token, time_fields, out)
    return pos + 1


def _consume_blocks(
    blocks: list[str],
    fields: tuple[str, ...],
) -> dict[str, int | Decimal]:
    out: dict[str, int | Decimal] = {}
    pos = 0
    pos = _consume_year_month_block(blocks, pos, fields, out)
    pos = _consume_day_block(blocks, pos, fields, out)
    pos = _consume_time_block(blocks, pos, fields, out)
    if pos != len(blocks):
        raise ValidationError(
            f"Unexpected extra tokens in INTERVAL literal: {' '.join(blocks[pos:])!r}",
        )
    return out


def parts_to_duckdb_expr(parts: IntervalParts) -> str:
    """Render :class:`IntervalParts` as an additive DuckDB INTERVAL expression.

    Zero components are dropped to keep the SQL readable. The empty
    interval (all-zero) renders as ``INTERVAL '0' SECOND`` so callers
    always get a valid INTERVAL-typed expression.
    """
    pieces: list[str] = []
    if parts.years:
        pieces.append(f"INTERVAL '{parts.years}' YEAR")
    if parts.months:
        pieces.append(f"INTERVAL '{parts.months}' MONTH")
    if parts.days:
        pieces.append(f"INTERVAL '{parts.days}' DAY")
    if parts.hours:
        pieces.append(f"INTERVAL '{parts.hours}' HOUR")
    if parts.minutes:
        pieces.append(f"INTERVAL '{parts.minutes}' MINUTE")
    if parts.seconds and parts.seconds != 0:
        # Render Decimal in canonical (no trailing zeros) form.
        s_str = format(parts.seconds.normalize(), "f")
        pieces.append(f"INTERVAL '{s_str}' SECOND")
    if not pieces:
        return "INTERVAL '0' SECOND"
    return "(" + " + ".join(pieces) + ")"


# ---------------------------------------------------------------------------
# JUSTIFY helpers — DuckDB has no justify_* scalar functions, so we emit
# a normalisation expression. ADR 0019 records the formulas; the test
# suite asserts the canonical results.
# ---------------------------------------------------------------------------


def justify_hours_expr(operand: str) -> str:
    """Emit a DuckDB expression equivalent to BigQuery ``JUSTIFY_HOURS(x)``.

    Pulls every full 24-hour micro-segment out of the seconds component
    into the day component.
    """
    return _justify_template(operand, justify_hours=True, justify_days=False)


def justify_days_expr(operand: str) -> str:
    """Emit a DuckDB expression equivalent to BigQuery ``JUSTIFY_DAYS(x)``.

    Pulls every full 30-day day-segment out of the day component into
    the month component (BigQuery and PostgreSQL agree on this rule).
    """
    return _justify_template(operand, justify_hours=False, justify_days=True)


def justify_interval_expr(operand: str) -> str:
    """Emit a DuckDB expression equivalent to ``JUSTIFY_INTERVAL(x)``.

    Applies both ``JUSTIFY_HOURS`` and ``JUSTIFY_DAYS`` rules.
    """
    return _justify_template(operand, justify_hours=True, justify_days=True)


_JUSTIFY_TEMPLATE = (
    "(to_months("
    "(extract('year' FROM {x})::BIGINT * 12 + extract('month' FROM {x})::BIGINT)"
    "{day_into_month}"
    ") + to_days("
    "{day_remain}"
    "{hour_into_day}"
    ") + to_hours("
    "{hour_remain}"
    ") + to_minutes("
    "extract('minute' FROM {x})::BIGINT"
    ") + to_microseconds("
    "(extract('microsecond' FROM {x}))::BIGINT"
    "))"
)


def _justify_template(operand: str, *, justify_hours: bool, justify_days: bool) -> str:
    x = f"({operand})"
    day_into_month = f" + (extract('day' FROM {x})::BIGINT // 30)" if justify_days else ""
    day_remain = f"extract('day' FROM {x})::BIGINT"
    if justify_days:
        day_remain = f"(extract('day' FROM {x})::BIGINT % 30)"
    hour_into_day = f" + (extract('hour' FROM {x})::BIGINT // 24)" if justify_hours else ""
    hour_remain = f"extract('hour' FROM {x})::BIGINT"
    if justify_hours:
        hour_remain = f"(extract('hour' FROM {x})::BIGINT % 24)"
    return _JUSTIFY_TEMPLATE.format(
        x=x,
        day_into_month=day_into_month,
        day_remain=day_remain,
        hour_into_day=hour_into_day,
        hour_remain=hour_remain,
    )


_NANOS_PER_SECOND = 1_000_000_000
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 60 * 60


def format_bq_interval(months: int, days: int, nanoseconds: int) -> str:
    """Format an interval ``(months, days, nanoseconds)`` triple as a BQ string.

    The output uses BigQuery's canonical ``Y-M D H:M:S[.ffffff]`` form.
    Negative values produce a single leading ``-``; year and month
    components are folded into ``Y-M`` only when the months value is
    a multiple of 12 (otherwise we emit ``0-M``). Components are
    *not* justified — DuckDB's MonthDayNano fields preserve their
    original components, and BigQuery emits them faithfully.

    Args:
        months: Whole-month component (signed).
        days: Whole-day component (signed).
        nanoseconds: Sub-day component, in nanoseconds (signed).

    Returns:
        The BigQuery-canonical interval string.
    """
    sign = "-" if (months < 0 or days < 0 or nanoseconds < 0) else ""
    m_abs = abs(months)
    d_abs = abs(days)
    n_abs = abs(nanoseconds)
    years, sub_months = divmod(m_abs, 12)
    total_seconds, frac_nanos = divmod(n_abs, _NANOS_PER_SECOND)
    hours, rem_seconds = divmod(total_seconds, _SECONDS_PER_HOUR)
    minutes, seconds = divmod(rem_seconds, _SECONDS_PER_MINUTE)
    if frac_nanos:
        # Render fractional with up to 6 digits (microsecond precision).
        micro = frac_nanos // 1000
        secs_str = f"{seconds}.{micro:06d}".rstrip("0").rstrip(".")
    else:
        secs_str = str(seconds)
    return f"{sign}{years}-{sub_months} {d_abs} {hours}:{minutes}:{secs_str}"


__all__ = [
    "IntervalParts",
    "format_bq_interval",
    "justify_days_expr",
    "justify_hours_expr",
    "justify_interval_expr",
    "parse_interval_literal",
    "parts_to_duckdb_expr",
]
