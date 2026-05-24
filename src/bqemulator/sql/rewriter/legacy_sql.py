"""Narrow legacy-SQL → standard-SQL pre-translator.

BigQuery accepts two SQL dialects: **Standard SQL** (the default) and
**Legacy SQL** (the original 2011-era dialect retained for backward
compatibility). Legacy SQL has its own parser, function catalogue,
identifier-quoting rules, scoping rules, and JOIN syntax — supporting
the full surface would double the translator burden. ADR
``out-of-scope.md#legacy-sql-uselegacysqltrue`` documents the v1.0
deferral of full legacy SQL support.

This module ships the **narrow subset** of legacy → standard rewrites
that are strict syntactic substitutions: type-cast functions and the
``[project:dataset.table]`` reference shape. Queries that use legacy-
SQL features outside this subset (JOIN EACH, WITHIN, FLATTEN, the
implicit-correlated-subquery rules, the ``date_add(NOW(), -7, 'DAY')``
form, etc.) still fall through to the standard pipeline and surface
the appropriate translation error. The handful of clients that only
needed legacy SQL for type-casts now see a clean PASS.
"""

from __future__ import annotations

import re

#: Legacy SQL type-cast functions and their standard-SQL replacement
#: type. Each entry rewrites ``LEGACY_NAME(<arg>)`` →
#: ``CAST(<arg> AS <STANDARD_TYPE>)``. Documented at
#: https://cloud.google.com/bigquery/docs/reference/legacy-sql#type-conversion-functions.
_LEGACY_CAST_FUNCTIONS: dict[str, str] = {
    "INTEGER": "INT64",
    "FLOAT": "FLOAT64",
    "STRING": "STRING",
    "BOOLEAN": "BOOL",
    "BYTES": "BYTES",
}

#: Match ``[project:dataset.table]`` legacy table references. The
#: capture groups recover the three components so the rewriter can
#: emit the standard ``\`project.dataset.table\``` form.
_LEGACY_TABLE_REF_RE = re.compile(
    r"\[(?P<project>[A-Za-z0-9_\-]+)"
    r":(?P<dataset>[A-Za-z0-9_]+)"
    r"\.(?P<table>[A-Za-z0-9_]+)\]",
)


def rewrite_legacy_to_standard(bq_sql: str) -> str:
    """Return ``bq_sql`` with the legacy-SQL subset rewritten to standard SQL.

    The rewrites are:

    * ``INTEGER(x)`` / ``FLOAT(x)`` / ``STRING(x)`` / ``BOOLEAN(x)`` /
      ``BYTES(x)`` → ``CAST(x AS INT64 | FLOAT64 | STRING | BOOL |
      BYTES)``;
    * ``[project:dataset.table]`` → ```project.dataset.table```.

    Queries using legacy-SQL features outside this subset survive
    unchanged; the standard pipeline raises the appropriate
    translation error.
    """
    out = bq_sql
    for legacy_name, standard_type in _LEGACY_CAST_FUNCTIONS.items():
        out = _rewrite_call_to_cast(out, legacy_name, standard_type)
    return _LEGACY_TABLE_REF_RE.sub(
        lambda m: f"`{m['project']}.{m['dataset']}.{m['table']}`",
        out,
    )


def _rewrite_call_to_cast(sql: str, fn_name: str, target_type: str) -> str:
    """Rewrite every ``<FN>(<arg>)`` call in ``sql`` to ``CAST(<arg> AS <type>)``.

    Uses a balanced-paren walk so nested calls inside the argument
    round-trip correctly (e.g. ``INTEGER(SAFE_CAST(x AS FLOAT64))``
    rewrites only the outer ``INTEGER`` call).
    """
    pattern = re.compile(rf"\b{fn_name}\s*\(", re.IGNORECASE)
    out: list[str] = []
    pos = 0
    while True:
        match = pattern.search(sql, pos)
        if match is None:
            out.append(sql[pos:])
            return "".join(out)
        out.append(sql[pos : match.start()])
        # Find the matching close paren via balanced-paren scan.
        arg_start = match.end()
        depth = 1
        i = arg_start
        while i < len(sql) and depth > 0:
            char = sql[i]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            i += 1
        if depth != 0:
            # Unbalanced parens — leave the rest untouched.
            out.append(sql[match.start() :])
            return "".join(out)
        arg = sql[arg_start : i - 1]
        out.append(f"CAST({arg} AS {target_type})")
        pos = i


__all__ = ["rewrite_legacy_to_standard"]
