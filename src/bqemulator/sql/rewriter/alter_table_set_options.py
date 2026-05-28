r"""Pre-translator: collapse ``ALTER TABLE ... SET OPTIONS(...)`` to a no-op.

BigQuery accepts ``ALTER TABLE \`proj\`.\`ds\`.\`t\` SET OPTIONS(...)``
as the canonical way to set / clear table-level metadata
(``description``, ``labels``, ``expiration_timestamp``, etc.).
``dbt-bigquery`` emits the bare ``SET OPTIONS()`` form (with empty
parens) at the tail of every ``dbt seed`` / ``dbt run`` to clear
prior options on a managed table.

DuckDB doesn't support ``ALTER TABLE ... SET OPTIONS``. SQLGlot's
BigQuery → DuckDB transpile gives up partway through and emits the
*unparseable* truncated form ``ALTER TABLE "..." SET`` (no clause
after ``SET``), which then trips DuckDB's parser with::

    Parser Error: syntax error at end of input

bqemulator already doesn't model table-level option metadata
(``options`` is a v1.0.1 polish item), so the right behaviour is to
no-op the statement — exactly what real BigQuery does for an empty
``SET OPTIONS()`` call on a table with no options to clear.

This module rewrites any ``ALTER TABLE … SET OPTIONS(…)`` (with or
without contents in the parens) into ``SELECT 1`` so DuckDB executes
a trivially-successful query and the job-state machine reports
success. The metadata-aware future implementation will replace this
rule with a catalog-side update.
"""

from __future__ import annotations

#: SQL keywords/forms we recognise. Kept as case-folded literals; the
#: scanner below compares against ``sql.casefold()`` so we don't need
#: a case-insensitive regex (which was a CodeQL ReDoS source on
#: prior revisions).
_ALTER = "alter"
_TABLE = "table"
_SET = "set"
_OPTIONS = "options"


def _strip_leading_comments(sql: str) -> int:
    """Return the index of the first non-comment, non-whitespace character.

    Hand-rolled scanner rather than a regex because the previous
    ``re``-based implementation tripped CodeQL's ReDoS detector
    (polynomial backtracking on inputs with many repeated comment-
    opener candidates). This scanner is linear in ``len(sql)``.
    """
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "-" and _starts_with(sql, i, "--"):
            i = _skip_line_comment(sql, i + 2)
            continue
        if ch == "/" and _starts_with(sql, i, "/*"):
            i = _skip_block_comment(sql, i + 2)
            continue
        return i
    return n


def _starts_with(sql: str, index: int, marker: str) -> bool:
    """Return True if ``sql[index:]`` begins with ``marker`` without overrunning."""
    return index + len(marker) <= len(sql) and sql[index : index + len(marker)] == marker


def _skip_line_comment(sql: str, start: int) -> int:
    r"""Advance past a ``-- …\n`` comment body and return the post-newline index."""
    n = len(sql)
    i = start
    while i < n and sql[i] != "\n":
        i += 1
    if i < n:
        i += 1  # consume the newline itself
    return i


def _skip_block_comment(sql: str, start: int) -> int:
    """Advance past a ``/* … */`` block; an unterminated block consumes to EOF."""
    end = sql.find("*/", start)
    if end == -1:
        return len(sql)
    return end + 2


def _peek_word(sql: str, start: int) -> tuple[str, int]:
    """Read the next contiguous run of non-whitespace chars.

    Returns ``(word, next_index)``. ``word`` is empty when ``start``
    is past the end. Used by :func:`rewrite_alter_table_set_options`
    to tokenise ``ALTER TABLE ... SET OPTIONS`` without a regex.
    """
    n = len(sql)
    i = start
    while i < n and not sql[i].isspace():
        i += 1
    return sql[start:i], i


def _skip_ws(sql: str, start: int) -> int:
    """Return the index of the next non-whitespace char at or after ``start``."""
    n = len(sql)
    i = start
    while i < n and sql[i].isspace():
        i += 1
    return i


def rewrite_alter_table_set_options(bq_sql: str) -> str:
    r"""Return a no-op statement when ``bq_sql`` is ``ALTER TABLE ... SET OPTIONS(...)``.

    Returns the original SQL unchanged for every other shape.

    Tolerates a leading SQL comment prefix (e.g. dbt's
    ``/* {"app": "dbt", "node_id": …} */`` job-tagging block) so the
    dbt-emitted ``alter table \`p\`.\`d\`.\`t\` set OPTIONS()``
    matches even when wrapped in metadata commentary.

    Implementation is a hand-rolled, linear-time scanner — the
    previous ``re``-based version had two CodeQL-flagged polynomial
    regexes (one for the comment prefix, one for the keyword + body
    walk). A scanner is both faster and ReDoS-immune by construction.
    """
    cursor = _match_alter_table_header(bq_sql)
    if cursor is None:
        return bq_sql
    cursor = _advance_past_table_ref_to_set(bq_sql, cursor)
    if cursor is None:
        return bq_sql
    cursor = _match_options_keyword(bq_sql, cursor)
    if cursor is None:
        return bq_sql
    close = _match_balanced_parens(bq_sql, cursor)
    if close is None:
        return bq_sql
    if not _is_trailing_whitespace_or_semicolon(bq_sql, close + 1):
        return bq_sql
    return "SELECT 1"


def _match_alter_table_header(sql: str) -> int | None:
    """Skip leading comments + match ``ALTER TABLE``; return the cursor after, or None."""
    cursor = _strip_leading_comments(sql)
    if cursor >= len(sql):
        return None
    word, cursor = _peek_word(sql, cursor)
    if word.casefold() != _ALTER:
        return None
    cursor = _skip_ws(sql, cursor)
    word, cursor = _peek_word(sql, cursor)
    if word.casefold() != _TABLE:
        return None
    return cursor


def _advance_past_table_ref_to_set(sql: str, cursor: int) -> int | None:
    """Walk word-by-word until the ``SET`` keyword; return the cursor after it.

    BigQuery's table reference between TABLE and SET can be one or
    more whitespace-delimited tokens (backticked, dotted, …). Returns
    ``None`` when the input is exhausted before ``SET`` is seen.
    """
    n = len(sql)
    while cursor < n:
        cursor = _skip_ws(sql, cursor)
        word, cursor = _peek_word(sql, cursor)
        if not word:
            return None
        if word.casefold() == _SET:
            return cursor
    return None


def _match_options_keyword(sql: str, cursor: int) -> int | None:
    """Match the literal ``OPTIONS`` token after ``SET``; return the cursor after it.

    ``OPTIONS`` may butt directly up against ``(``, so the peeked word
    can capture ``OPTIONS(``; the parenthesis is trimmed off and the
    cursor is set to point at the opening paren.
    """
    cursor = _skip_ws(sql, cursor)
    word, after_options = _peek_word(sql, cursor)
    keyword = word
    paren_offset = keyword.find("(")
    if paren_offset != -1:
        after_options = cursor + paren_offset
        keyword = keyword[:paren_offset]
    if keyword.casefold() != _OPTIONS:
        return None
    return after_options


def _match_balanced_parens(sql: str, cursor: int) -> int | None:
    """Verify ``(`` at ``cursor`` (after optional WS) + return index of matching ``)``.

    BigQuery option bodies are scalars/strings/arrays — no nested
    function calls, so a single-level find is enough.
    """
    cursor = _skip_ws(sql, cursor)
    if cursor >= len(sql) or sql[cursor] != "(":
        return None
    close = sql.find(")", cursor + 1)
    if close == -1:
        return None
    return close


def _is_trailing_whitespace_or_semicolon(sql: str, start: int) -> bool:
    """Return True if the slice from ``start`` is just whitespace + at most one ``;``."""
    tail = _skip_ws(sql, start)
    if tail < len(sql) and sql[tail] == ";":
        tail += 1
    tail = _skip_ws(sql, tail)
    return tail == len(sql)


__all__ = ["rewrite_alter_table_set_options"]
