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
        # Line comment: ``-- …\n``.
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            i += 2
            while i < n and sql[i] != "\n":
                i += 1
            if i < n:
                i += 1  # consume the newline itself
            continue
        # Block comment: ``/* … */``.
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                return n  # unterminated — treat the whole thing as comment
            i = end + 2
            continue
        return i
    return n


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
    # 1. Skip leading whitespace + SQL comments.
    cursor = _strip_leading_comments(bq_sql)
    if cursor >= len(bq_sql):
        return bq_sql

    # 2. Expect ``ALTER`` (case-insensitive).
    word, cursor = _peek_word(bq_sql, cursor)
    if word.casefold() != _ALTER:
        return bq_sql

    # 3. Expect ``TABLE``.
    cursor = _skip_ws(bq_sql, cursor)
    word, cursor = _peek_word(bq_sql, cursor)
    if word.casefold() != _TABLE:
        return bq_sql

    # 4. Walk word-by-word until we see ``SET`` (the table reference
    #    between TABLE and SET can be one or more whitespace-delimited
    #    tokens — backticked, dotted, etc.). Bail if we run out.
    while cursor < len(bq_sql):
        cursor = _skip_ws(bq_sql, cursor)
        word, cursor = _peek_word(bq_sql, cursor)
        if not word:
            return bq_sql
        if word.casefold() == _SET:
            break

    # 5. Expect ``OPTIONS``.
    cursor = _skip_ws(bq_sql, cursor)
    word, after_options = _peek_word(bq_sql, cursor)
    # ``OPTIONS`` may butt directly up against ``(``, so peek_word
    # could capture ``OPTIONS(...``; trim the parenthesis if so.
    keyword = word
    paren_offset = keyword.find("(")
    if paren_offset != -1:
        after_options = cursor + paren_offset
        keyword = keyword[:paren_offset]
    if keyword.casefold() != _OPTIONS:
        return bq_sql

    # 6. The next non-whitespace char must be ``(``. Then find the
    #    matching ``)`` (BigQuery option values are scalars/strings/
    #    arrays — no nested function calls, so single-level balance
    #    is enough).
    cursor = _skip_ws(bq_sql, after_options)
    if cursor >= len(bq_sql) or bq_sql[cursor] != "(":
        return bq_sql
    close = bq_sql.find(")", cursor + 1)
    if close == -1:
        return bq_sql

    # 7. Everything after ``)`` must be whitespace + optional ``;``.
    tail = _skip_ws(bq_sql, close + 1)
    if tail < len(bq_sql) and bq_sql[tail] == ";":
        tail += 1
    tail = _skip_ws(bq_sql, tail)
    if tail != len(bq_sql):
        return bq_sql

    return "SELECT 1"


__all__ = ["rewrite_alter_table_set_options"]
