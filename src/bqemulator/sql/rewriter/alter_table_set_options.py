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

import re

#: Match leading SQL line comments (``-- …``) and block comments
#: (``/* … */``) so prefixes like dbt's ``/* {"app": "dbt", …} */``
#: don't defeat the ``^`` anchor.
_LEADING_COMMENT_RE = re.compile(
    r"""
    \s*
    (?:
        --[^\n]*\n           # line comment up to newline
      | /\*.*?\*/            # block comment (non-greedy)
    )
    \s*
    """,
    re.DOTALL | re.VERBOSE,
)


#: Match ``ALTER TABLE <anything> SET OPTIONS(<anything>)`` with
#: arbitrary whitespace and (balanced or empty) parenthesised content.
#: ``re.DOTALL`` so multiline OPTIONS bodies still match;
#: ``re.IGNORECASE`` so ``alter table … set options(…)`` (the form
#: dbt emits in lowercase) is caught.
_ALTER_TABLE_SET_OPTIONS_RE = re.compile(
    r"""
    ^\s*
    ALTER\s+TABLE\s+        # leading keyword
    .+?                     # table reference (non-greedy)
    \s+SET\s+OPTIONS\s*     # the SET OPTIONS clause
    \(                      # opening paren
    [^()]*                  # body (no nested parens — BQ option
                            # values are scalars, strings, or arrays
                            # of scalars; never nested function calls)
    \)                      # closing paren
    \s*;?\s*$               # optional trailing semicolon
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


def _strip_leading_comments(sql: str) -> str:
    """Drop leading SQL comments (line + block) so the ``^`` anchor below sees the keyword."""
    prev = None
    while prev != sql:
        prev = sql
        sql = _LEADING_COMMENT_RE.sub("", sql, count=1)
    return sql


def rewrite_alter_table_set_options(bq_sql: str) -> str:
    r"""Return a no-op statement when ``bq_sql`` is ``ALTER TABLE ... SET OPTIONS(...)``.

    Returns the original SQL unchanged for every other shape.

    Tolerates a leading SQL comment prefix (e.g. dbt's
    ``/* {"app": "dbt", "node_id": …} */`` job-tagging block) so the
    dbt-emitted ``alter table \`p\`.\`d\`.\`t\` set OPTIONS()``
    matches even when wrapped in metadata commentary.
    """
    stripped = _strip_leading_comments(bq_sql)
    if _ALTER_TABLE_SET_OPTIONS_RE.match(stripped):
        return "SELECT 1"
    return bq_sql


__all__ = ["rewrite_alter_table_set_options"]
