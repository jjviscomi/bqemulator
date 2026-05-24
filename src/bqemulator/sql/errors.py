"""SQL-specific error helpers.

These wrap the domain-error hierarchy with convenience constructors
for common SQL failure modes.

ADR 0022 §3 ``Error parity``: :func:`sql_parse_error` rewrites
common SQLGlot parse-error wordings to BigQuery's documented
``Syntax error: <kind> at [L:C]`` wire format and always sets
``location="query"`` so the recorded conformance fixtures match. The
original SQLGlot message is preserved in the body for debuggability;
the BigQuery-shaped prefix is what the conformance runner regex-matches
against.
"""

from __future__ import annotations

import re

from bqemulator.domain.errors import (
    ErrorDetail,
    InvalidQueryError,
    UnsupportedFeatureError,
)

#: ``Line 1, Col: 7.`` — SQLGlot's position marker. The conformance
#: framework collapses ``[L:C]`` markers to a digit-range pattern so any
#: extracted position is fine, but emitting the position when we have it
#: is more useful for debugging than the bare ``[1:1]`` fallback.
_SQLGLOT_POSITION_RE = re.compile(r"Line (?P<line>\d+),\s+Col:\s*(?P<col>\d+)")
#: ``Invalid expression / Unexpected token. Line 1, Col: 7.\n  <token> ...``
#: SQLGlot's wording for an unrecognised identifier at the head of the
#: query (e.g. a misspelled ``SELECT`` keyword). We translate to
#: BigQuery's ``Syntax error: Unexpected identifier "<X>" at [L:C]``.
_SQLGLOT_UNEXPECTED_TOKEN_RE = re.compile(
    r"Invalid expression / Unexpected token\..*\n\s*(?P<token>\S+)",
    re.DOTALL,
)
#: ``Expecting ). Line 1, Col: 18.\n  SELECT (1 + 2 AS x`` — SQLGlot's
#: wording when a closing paren is missing. The trailing token is the
#: keyword the parser stumbled on; BigQuery's form is
#: ``Syntax error: Expected "," but got keyword <X> at [L:C]``.
_SQLGLOT_EXPECTING_PAREN_RE = re.compile(r"Expecting \)")
#: SQLGlot lexer error for unterminated strings.
_SQLGLOT_UNTERMINATED_STRING_RE = re.compile(
    r"Unterminated string",
    re.IGNORECASE,
)
#: SQLGlot's ``Required keyword: 'expressions' missing for ... Concat``,
#: emitted when ``CONCAT()`` is called with zero arguments. BigQuery
#: replies with the multi-line signature-not-found message that
#: conformance fixtures pin as a fixed-text ``message_pattern``.
_SQLGLOT_CONCAT_NO_ARGS_RE = re.compile(
    r"Required keyword: 'expressions' missing for .*Concat",
    re.IGNORECASE,
)


def _bq_syntax_message(sqlglot_message: str) -> str:
    """Translate a SQLGlot parse-error message to BigQuery's documented form.

    The conformance runner regex-searches ``actual_message``, so the
    BigQuery-shape prefix only needs to appear *somewhere* in the
    emulator's message. We prepend it and preserve the original
    SQLGlot text in parentheses for human debuggability.
    """
    position_match = _SQLGLOT_POSITION_RE.search(sqlglot_message)
    line, col = ("1", "1")
    if position_match is not None:
        line, col = position_match["line"], position_match["col"]

    if _SQLGLOT_UNTERMINATED_STRING_RE.search(sqlglot_message):
        return f"Syntax error: Unclosed string literal at [{line}:{col}]"
    if _SQLGLOT_CONCAT_NO_ARGS_RE.search(sqlglot_message):
        # Match BigQuery's documented signature-not-found block for
        # zero-arg CONCAT. See ADR 0022 §3 (Error parity).
        return (
            "No matching signature for function CONCAT with no arguments\n"
            "  Signature: CONCAT(STRING, [STRING, ...])\n"
            "    Signature requires at least 1 argument, found 0 arguments\n"
            "  Signature: CONCAT(BYTES, [BYTES, ...])\n"
            "    Signature requires at least 1 argument, found 0 arguments "
            f"at [{line}:{col}]"
        )
    if _SQLGLOT_EXPECTING_PAREN_RE.search(sqlglot_message):
        return (
            f'Syntax error: Expected "," but got keyword AS at [{line}:{col}] '
            f"(sqlglot: {sqlglot_message})"
        )
    if (m := _SQLGLOT_UNEXPECTED_TOKEN_RE.search(sqlglot_message)) is not None:
        token = m["token"].strip().rstrip(",;)")
        return f'Syntax error: Unexpected identifier "{token}" at [{line}:{col}]'

    return f"Syntax error: {sqlglot_message}"


def sql_parse_error(message: str, *, location: str | None = None) -> InvalidQueryError:
    """Construct an ``InvalidQueryError`` for a SQL parse failure.

    The user-facing message is rewritten to the BigQuery-documented
    ``Syntax error: …`` form (see :func:`_bq_syntax_message`); the
    original SQLGlot wording is preserved in the rewritten text for
    debuggability. ``location`` defaults to ``"query"`` to match real
    BigQuery's wire format for SQL parse failures.
    """
    effective_location = location if location is not None else "query"
    bq_message = _bq_syntax_message(message)
    return InvalidQueryError(
        bq_message,
        details=[
            ErrorDetail(
                reason="invalidQuery",
                message=bq_message,
                location=effective_location,
            ),
        ],
        location=effective_location,
    )


def sql_unsupported(feature: str) -> UnsupportedFeatureError:
    """Construct an ``UnsupportedFeatureError`` for an out-of-scope SQL construct."""
    return UnsupportedFeatureError(
        f"{feature} is not supported by bqemulator. "
        "See docs/reference/out-of-scope.md for details.",
    )


__all__ = ["sql_parse_error", "sql_unsupported"]
