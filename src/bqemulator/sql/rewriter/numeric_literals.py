"""Pre-translator rewriter for BigQuery NUMERIC / BIGNUMERIC literals.

BigQuery's ``NUMERIC '…'`` and ``BIGNUMERIC '…'`` typed literals parse
as :class:`exp.Cast` with the wide-typed ``DECIMAL`` / ``BIGDECIMAL``
target but *no* precision/scale parameters. SQLGlot's DuckDB generator
then emits ``CAST(... AS DECIMAL)`` — which DuckDB resolves to its
default ``DECIMAL(18, 3)``. That precision is too narrow for
BigQuery's 38-digit ``NUMERIC`` range and any value over the default
errors at execution time with ``Conversion Error: Could not convert
string … to DECIMAL(18,3)``.

We rewrite each naked NUMERIC / BIGNUMERIC literal to an explicit
DuckDB representation *before* the transpile so the precision +
type-tag survives:

* ``NUMERIC '…'``    → ``CAST('…' AS DECIMAL(38, 9))``.

* ``BIGNUMERIC '…'`` — scale-aware dispatch:
  - **Path A — fits exactly.** If the literal's natural scale (count
    of fractional digits) is ≥ 10 *and* the total digit count is
    ≤ 38, cast directly to ``DECIMAL(38, S)`` where
    ``S = natural_scale``. This handles the high-precision case
    (e.g. 0 integer + 38 fractional digits — DECIMAL(38, 38)
    preserves every digit).
  - **Path B — wide-integer via UDF.** If the literal's total digit
    count is ≤ 38 but its natural scale is < 10, route through the
    ``bqemu_to_bignumeric`` Python UDF returning ``DECIMAL(38, 10)``.
    The scale of 10 (> 9) is the marker the schema renderer uses to
    surface BIGNUMERIC, and the Python-side :class:`Decimal` parser
    sidesteps DuckDB's literal default of DECIMAL(18, 3).
  - **Path C — fractional truncation.** If the total digit count
    exceeds 38 BUT the integer part still fits ``DECIMAL(38, 0)``,
    drop fractional digits until the value fits ``DECIMAL(38, S)``
    where ``S = 38 - integer_digits``. This trades some
    least-significant fractional precision for representability —
    matches BigQuery's contract better than raising a Conversion
    Error (BQ accepts the literal; the emulator now also accepts it
    with documented precision loss for the last few digits). See
    `out-of-scope.md#bignumeric-literals-with-39-integer-digits`.

Either of paths A or B preserves the wire-format type tag
(BIGNUMERIC) and the value exactly. Path C preserves the wire-format
type tag and the integer part exactly, with up to
``natural_scale - (38 - integer_digits)`` digits of fractional
truncation.

BIGNUMERIC literals where ``integer_digits > 38`` still cannot be
represented at any scale — DuckDB's DECIMAL(38, 0) caps at 38 integer
digits. ``bound_bignumeric_max`` is the canonical example (39 integer
+ 38 fractional digits, i.e. BigQuery's BIGNUMERIC maximum value).
Those cascade to the ``bqemu_to_bignumeric`` Python UDF which raises
``Invalid BIGNUMERIC literal`` — and the fixture remains XFAILed
against the documented backend cap.
"""

from __future__ import annotations

import re

_NUMERIC_PRECISION = 38
_NUMERIC_SCALE = 9
_BIGNUMERIC_UDF_SCALE = 10

# ``NUMERIC '…'`` and ``BIGNUMERIC '…'`` are BigQuery's typed-literal
# syntax — the integer / scale never appears in the source. We match
# the keyword as a word boundary so identifiers ending in ``NUMERIC``
# (e.g. ``MY_NUMERIC``) are untouched, and accept either single or
# double quotes for the literal body.
_NUMERIC_RE = re.compile(r"(?i)\bNUMERIC\s+(['\"])([^'\"]+)\1")
_BIGNUMERIC_RE = re.compile(r"(?i)\bBIGNUMERIC\s+(['\"])([^'\"]+)\1")


def rewrite_numeric_literals(bq_sql: str) -> str:
    """Pre-translate BigQuery NUMERIC / BIGNUMERIC typed literals.

    Replaces each ``NUMERIC 'literal'`` with
    ``CAST('literal' AS DECIMAL(38, 9))`` and each ``BIGNUMERIC
    'literal'`` with either an explicit-scale CAST (for high-precision
    fractional values) or a ``bqemu_to_bignumeric('literal')`` UDF
    call (for wide-integer values). SQLGlot's BigQuery serialiser
    collapses ``DECIMAL(38, 9)`` back to bare ``NUMERIC`` (precision is
    implicit in the BQ type name), so the rewrite is done at the
    string level instead of through the AST.

    Returns the input unchanged when no NUMERIC / BIGNUMERIC literal
    appears (the common case).
    """
    upper = bq_sql.upper()
    if "NUMERIC" not in upper:
        return bq_sql
    rewritten = _BIGNUMERIC_RE.sub(_rewrite_bignumeric, bq_sql)
    return _NUMERIC_RE.sub(
        lambda m: (
            f"CAST({m.group(1)}{m.group(2)}{m.group(1)} AS DECIMAL({_NUMERIC_PRECISION}, "
            f"{_NUMERIC_SCALE}))"
        ),
        rewritten,
    )


def _rewrite_bignumeric(match: re.Match[str]) -> str:
    """Choose the BIGNUMERIC dispatch based on the literal's digit profile.

    Three dispatch paths (see module docstring for the full contract):

    * **Path A — fits exactly**: ``fractional ≥ 10 AND total ≤ 38``
      → direct CAST with explicit scale.
    * **Path B — wide-integer via UDF**: ``total ≤ 38`` → ``bqemu_to_bignumeric``
      Python UDF returning ``DECIMAL(38, 10)``.
    * **Path C — fractional truncation**: ``total > 38 AND integer ≤ 38``
      → direct CAST with the fractional part truncated to ``38 - integer``
      digits. Trades fractional precision for representability.
    """
    quote = match.group(1)
    literal = match.group(2)
    integer_digits, fractional_digits = _split_decimal_digits(literal)
    total_digits = integer_digits + fractional_digits

    # Path A: literal fits cleanly with its natural scale.
    if fractional_digits >= _BIGNUMERIC_UDF_SCALE and total_digits <= _NUMERIC_PRECISION:
        scale = fractional_digits
        return f"CAST({quote}{literal}{quote} AS DECIMAL({_NUMERIC_PRECISION}, {scale}))"

    # Path C: literal overflows but the integer part still fits — drop
    # fractional precision to make it fit. The truncated literal's scale
    # is ``38 - integer_digits``; the schema renderer's "scale > 9 →
    # BIGNUMERIC" rule fires whenever ``38 - integer_digits > 9`` (i.e.
    # ``integer_digits < 29``). For wider integers (e.g. 30 int + 8
    # frac), the scale collapses to ≤ 9 and the renderer surfaces the
    # column as NUMERIC. That's an acknowledged corner case: BigQuery's
    # column type is determined by the SCHEMA, not the literal's scale,
    # so a wide-integer BIGNUMERIC literal bound into a BIGNUMERIC
    # column lands correctly; a bare ``SELECT BIGNUMERIC '…'`` with
    # ``integer_digits ≥ 29 AND total > 38`` surfaces as NUMERIC on the
    # wire (documented in `out-of-scope.md`).
    if total_digits > _NUMERIC_PRECISION and integer_digits <= _NUMERIC_PRECISION:
        max_scale = _NUMERIC_PRECISION - integer_digits
        truncated = _truncate_fractional(literal, max_scale)
        return f"CAST({quote}{truncated}{quote} AS DECIMAL({_NUMERIC_PRECISION}, {max_scale}))"

    # Path B: fall back to the Python helper. Tolerates integer-side
    # counts up to 28 digits without depending on DuckDB's literal
    # default. Values exceeding DECIMAL(38, 10)'s capacity (>= 29
    # integer digits if total <= 38, or >= 39 integer digits if Path C
    # truncation was skipped because integer > 38) raise
    # ``Invalid BIGNUMERIC literal`` — the documented limit.
    return f"bqemu_to_bignumeric({quote}{literal}{quote})"


def _truncate_fractional(literal: str, max_scale: int) -> str:
    """Truncate the fractional part of *literal* to at most *max_scale* digits.

    Used by the BIGNUMERIC pre-translator's overflow branch (Path C):
    when a literal's integer part fits ``DECIMAL(38, 0)`` but the
    combined integer + fractional digit count exceeds 38, dropping
    fractional digits brings the value back within DuckDB's
    representable range. Truncation (not rounding) is the chosen
    semantic — it matches the "drop precision the backend cannot
    hold" contract documented in
    `out-of-scope.md#bignumeric-literals-with-39-integer-digits`.

    The optional leading sign is preserved; a literal with no
    fractional part round-trips unchanged. When ``max_scale`` is 0 or
    negative, the fractional part (including the decimal point) is
    dropped entirely so the emitted CAST target is ``DECIMAL(38, 0)``
    rather than ``DECIMAL(38, -N)`` (DuckDB rejects negative scale).
    """
    body = literal.strip()
    if "." not in body:
        return body
    integer, _, fractional = body.partition(".")
    if max_scale <= 0:
        return integer
    return f"{integer}.{fractional[:max_scale]}"


def _split_decimal_digits(literal: str) -> tuple[int, int]:
    """Return ``(significant_integer_digits, fractional_digit_count)``.

    Strips the optional leading sign and ignores any non-digit
    characters except the single decimal point. Leading zeros in the
    integer part don't count toward DECIMAL precision capacity — a
    literal like ``0.1234`` is representable as ``DECIMAL(4, 4)``
    even though the literal text contains a ``0`` before the
    decimal point. Scientific-notation BIGNUMERIC literals are not in
    scope (BigQuery does not accept them in ``BIGNUMERIC '…'`` form).
    """
    body = literal.strip().lstrip("+-")
    if "." not in body:
        return len(body.lstrip("0")) or 1, 0
    integer, _, fractional = body.partition(".")
    integer_significant = integer.lstrip("0")
    return len(integer_significant), len(fractional)


__all__ = ["rewrite_numeric_literals"]
