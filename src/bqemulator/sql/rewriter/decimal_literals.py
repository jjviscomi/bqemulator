"""Pre-translator rewriter that pins bare BigQuery decimal literals to FLOAT64.

BigQuery's literal-type inference treats any unqualified fixed-point
decimal — ``3.25``, ``-1.5``, ``0.0`` — as ``FLOAT64``. DuckDB instead
infers the narrowest ``DECIMAL(p, s)`` that fits the literal text, so
``SELECT 3.25`` lands as ``DECIMAL(3, 2)`` and downstream functions
(``CEIL`` / ``FLOOR`` / ``ROUND`` / ``TRUNC``) preserve the DECIMAL
output type. The REST schema renderer then surfaces the column as
``NUMERIC`` even though BigQuery would have returned ``FLOAT``.

We rewrite each bare decimal literal to scientific notation
(``3.25`` → ``3.25e0``) *before* the SQLGlot transpile. DuckDB parses
the ``E0`` form as ``DOUBLE`` — which our Arrow→BigQuery type mapper
already renders as ``FLOAT`` — so the result column type matches
BigQuery's expectation.

The rewrite is done at the string level (not via the SQLGlot AST):
parsing and re-serialising the BigQuery AST is destructive for some
non-trivial constructs the slice-2 corpus exercises — notably
``VALUES (...) AS t(col)``, which SQLGlot re-emits as
``UNNEST([STRUCT(... AS _c0)])`` and loses the column alias.
Regex-driven substitution preserves the surrounding SQL byte-for-byte
and only touches the matched literal text.

The regex skips:

* literals inside string contexts (``'…'`` / ``"…"``) — the bodies of
  ``NUMERIC '…'`` / ``BIGNUMERIC '…'`` / ``DATE '…'`` / ``INTERVAL '…'``
  typed literals all live in quoted strings;
* literals already in scientific form (``3.25e0`` is not matched);
* numbers without a decimal point (integer literals);
* identifier-adjacent matches (``column_3.25`` does not touch the
  ``3.25`` even though it looks like a decimal — the lookbehind
  excludes identifier characters).
"""

from __future__ import annotations

import re

# A bare decimal literal: ``digits.digits`` not preceded by an
# identifier character (so ``foo_3.25`` is *not* a match) and not
# followed by an identifier character (so ``3.25e0`` and
# ``3.25_abc`` are skipped).
_DECIMAL_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d+)\.(\d+)(?![A-Za-z0-9_.])")


def rewrite_decimal_literals(bq_sql: str) -> str:
    """Pin bare BigQuery decimal literals to ``FLOAT64``-typed form.

    Returns the input unchanged when no candidate literal is present
    (the common path — checked via a fast ``.`` substring search).
    """
    if "." not in bq_sql:
        return bq_sql
    parts: list[str] = []
    i = 0
    n = len(bq_sql)
    while i < n:
        ch = bq_sql[i]
        if ch in {"'", '"'}:
            end = _find_string_end(bq_sql, i, ch)
            parts.append(bq_sql[i:end])
            i = end
            continue
        # Run the regex over the next contiguous non-string segment.
        segment_end = _find_next_quote(bq_sql, i)
        segment = bq_sql[i:segment_end]
        parts.append(_DECIMAL_RE.sub(r"\1.\2e0", segment))
        i = segment_end
    return "".join(parts)


def _find_string_end(sql: str, start: int, quote: str) -> int:
    r"""Return one-past-end index of the string literal at ``sql[start]``.

    The literal includes both delimiters. Backslash escapes inside the
    string are honoured (``'it\'s'``); a closing-quote-doubling
    convention (``'it''s'``) is treated as an in-string quote pair so
    the literal continues. Truncated strings (no closing quote) extend
    to end-of-input — the downstream SQLGlot transpile will surface
    the syntax error.
    """
    i = start + 1
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == quote:
            if i + 1 < n and sql[i + 1] == quote:
                # SQL standard ``''`` escape inside a single-quoted
                # string. Skip both characters and keep scanning.
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _find_next_quote(sql: str, start: int) -> int:
    """Return the index of the next ``'`` or ``"`` at or after *start*."""
    i = start
    n = len(sql)
    while i < n and sql[i] != "'" and sql[i] != '"':
        i += 1
    return i


__all__ = ["rewrite_decimal_literals"]
