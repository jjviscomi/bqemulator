"""Pre-translator rewriter for BigQuery ``SHA512(x)``.

SQLGlot parses ``SHA512(x)`` into an :class:`exp.SHA2` node carrying a
``length=512`` argument, and then its BigQuery → DuckDB transpile
mistakenly drops the algorithm width — both ``SHA256(x)`` and
``SHA512(x)`` come out the other side as DuckDB ``SHA256(x)``. DuckDB's
native hash catalogue only contains ``sha1`` and ``sha256``, so we
route ``SHA512`` through a Python helper while the AST still carries
the original length annotation.

The rewriter replaces each ``SHA2(operand, length=512)`` with an
anonymous ``bqemu_sha512(operand)`` call. The helper returns ``BLOB``
matching BigQuery's ``BYTES`` wire-format, and the surrounding
``TO_HEX(...)`` / ``LOWER(HEX(...))`` wrappers compose naturally.

The function short-circuits when no SHA512 reference appears in the
input, keeping the common path zero-cost.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def rewrite_sha512(bq_sql: str) -> str:
    """Pre-translate every ``SHA512(x)`` BigQuery call to ``bqemu_sha512(x)``.

    Returns the input unchanged when no SHA512 reference appears (the
    common case). Parse failures fall through to the existing
    downstream error path.
    """
    if "SHA512" not in bq_sql.upper():
        return bq_sql
    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = False
    for node in list(parsed.walk()):
        if not isinstance(node, exp.SHA2):
            continue
        length_node = node.args.get("length")
        if length_node is None:
            continue
        if isinstance(length_node, exp.Literal) and str(length_node.this) != "512":
            continue
        operand = node.this
        if operand is None:
            continue
        replacement = exp.Anonymous(
            this="bqemu_sha512",
            expressions=[operand.copy()],
        )
        node.replace(replacement)
        modified = True

    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


__all__ = ["rewrite_sha512"]
