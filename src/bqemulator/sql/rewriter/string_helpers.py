"""Pre-translator rewriter for BigQuery string builtins (NORMALIZE + 4-arg INSTR).

ADR 0023 §1.J introduces the NORMALIZE family; ADR 0023 §1.I adds the
4-argument INSTR rewrite. Both rewrites preserve information the
default SQLGlot transpile would otherwise drop.

NORMALIZE family
================

SQLGlot parses both functions into the same :class:`exp.Normalize` node
with an ``is_casefold`` flag that distinguishes the two — and then
*discards* that flag during the DuckDB-side transpile. The downstream
output for both BigQuery calls is the identical
``NORMALIZE('Straße', NFC)`` string, so by the time the post-translator
rule pass runs we can no longer tell ``NORMALIZE_AND_CASEFOLD`` apart
from plain ``NORMALIZE``.

Rewriting *before* the transpile preserves the distinction. We map
each form to an :class:`exp.Anonymous` call against the Python helper
registered by :mod:`bqemulator.sql.builtin_udfs`:

* ``NORMALIZE(s, form)``               → ``bqemu_normalize(s, 'form')``
* ``NORMALIZE_AND_CASEFOLD(s, form)``  → ``bqemu_normalize_casefold(s, 'form')``
* ``NORMALIZE(s)`` (default NFC)       → ``bqemu_normalize(s, 'NFC')``

INSTR 4-argument form
======================

``INSTR(haystack, needle, position, occurrence)`` carries an
``occurrence`` argument that BigQuery uses to return the *occurrence*-th
match. SQLGlot's BQ → DuckDB transpile drops the argument and emits a
``CASE … STRPOS(SUBSTRING(haystack, position), needle) …`` form that
returns only the *first* match starting at ``position``. We replace the
4-arg ``StrPosition`` node with a call to the Python helper
:func:`bqemulator.sql.builtin_udfs.bqemu_instr_occurrence` while the
AST still carries the original shape.

The 2- and 3-argument forms are left alone — DuckDB's emulation
handles them correctly.

The function short-circuits when no NORMALIZE or INSTR appears in the
input, keeping the common path zero-cost.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

_FORM_KEYWORDS = frozenset({"NFC", "NFD", "NFKC", "NFKD"})


def rewrite_string_helpers(bq_sql: str) -> str:
    """Pre-translate BigQuery NORMALIZE / NORMALIZE_AND_CASEFOLD / 4-arg INSTR.

    Returns the input unchanged when none of the patterns apply (the
    common case). Parse failures fall through to the existing
    downstream error path.
    """
    upper = bq_sql.upper()
    needs_normalize = "NORMALIZE" in upper
    needs_instr = "INSTR" in upper
    if not (needs_normalize or needs_instr):
        return bq_sql
    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = False
    if needs_normalize:
        modified |= _rewrite_normalize_calls(parsed)
    if needs_instr:
        modified |= _rewrite_instr_4arg_calls(parsed)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _rewrite_normalize_calls(tree: exp.Expression) -> bool:
    """Walk *tree* and replace every ``exp.Normalize`` node in place.

    Returns ``True`` when at least one node was replaced. Nodes whose
    form operand can't be parsed as one of the four recognised
    keywords default to ``NFC`` (BigQuery's documented default).
    """
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.Normalize):
            continue
        operand = node.this.copy() if node.this is not None else exp.Null()
        form_text = _form_keyword(node.args.get("form")) or "NFC"
        helper_name = (
            "bqemu_normalize_casefold" if node.args.get("is_casefold") else "bqemu_normalize"
        )
        replacement = exp.Anonymous(
            this=helper_name,
            expressions=[operand, exp.Literal.string(form_text)],
        )
        node.replace(replacement)
        modified = True
    return modified


def _rewrite_instr_4arg_calls(tree: exp.Expression) -> bool:
    """Walk *tree* and replace every 4-arg ``INSTR`` with a UDF call.

    Returns ``True`` when at least one node was replaced. The 2- and
    3-argument ``StrPosition`` forms are left alone — DuckDB's
    emulation handles them.
    """
    modified = False
    for node in list(tree.find_all(exp.StrPosition)):
        if node.args.get("occurrence") is None:
            continue
        haystack = node.this
        needle = node.args.get("substr")
        position = node.args.get("position") or exp.Literal.number(1)
        occurrence = node.args.get("occurrence")
        if haystack is None or needle is None or occurrence is None:
            continue
        replacement = exp.Anonymous(
            this="bqemu_instr_occurrence",
            expressions=[
                haystack.copy(),
                needle.copy(),
                position.copy(),
                occurrence.copy(),
            ],
        )
        node.replace(replacement)
        modified = True
    return modified


def _form_keyword(node: exp.Expression | None) -> str | None:
    """Return the uppercased form keyword from a NORMALIZE form operand."""
    if node is None:
        return None
    candidate: str | None = None
    if isinstance(node, exp.Var):
        candidate = str(node.this)
    elif isinstance(node, exp.Column):
        candidate = node.name
    elif isinstance(node, exp.Literal) and node.is_string:
        candidate = str(node.this)
    if candidate is None:
        return None
    upper = candidate.upper()
    return upper if upper in _FORM_KEYWORDS else None


__all__ = ["rewrite_string_helpers"]
