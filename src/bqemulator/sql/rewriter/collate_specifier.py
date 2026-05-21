r"""Pre-translator rewriter for BigQuery ``COLLATE(value, specifier)``.

BigQuery's ``COLLATE(value, specifier)`` accepts a small set of named
collation specifiers — most prominently ``'und:ci'`` (the
case-insensitive Unicode default) and the explicitly-rejected
``'binary'`` (the BigQuery service returns
``Collation 'binary' in collate function is not supported.``).

SQLGlot parses both into the typed :class:`sqlglot.exp.Collate` node
(``this`` = value, ``expression`` = specifier literal). Its DuckDB
generator emits ``<value> COLLATE <specifier>`` *unquoted* — DuckDB's
parser then rejects ``COLLATE und:ci`` because the specifier is not a
valid DuckDB identifier (the colon ``:`` is the lexer divider). The
post-translate rule pass never sees the call because the transpile
fails earlier.

The pre-translator walks the BigQuery AST while ``Collate`` still
carries the specifier literal and rewrites the call to a DuckDB-safe
form:

* ``COLLATE(value, 'und:ci')`` → ``LOWER(value)``. The
  case-insensitive Unicode default folds case before comparison;
  Python and DuckDB's :func:`LOWER` both follow the same Unicode
  table BigQuery uses, so equality on lower-cased operands matches
  the documented case-insensitive collation semantic for the ASCII
  + common-Unicode characters the corpus exercises.
* ``COLLATE(value, 'binary')`` → ``error('Collation \\'binary\\' in
  collate function is not supported.')``. The recorded ``str_collate_binary``
  fixture is an error fixture; routing through DuckDB's ``error()``
  builtin surfaces an ``InvalidQueryError`` with the BigQuery wording
  through the existing :mod:`bqemulator.jobs.error_mapper` fallback.

Other specifiers (``en-US``, ``de-DE``, etc.) flow through
unrewritten so a future fixture exercising them would surface a
specific divergence the corpus can pin against. The current corpus
exercises only ``und:ci`` and ``binary`` — the two BigQuery-documented
values most users reach for.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

_BINARY_ERROR_MESSAGE = "Collation 'binary' in collate function is not supported."
_CASE_INSENSITIVE_SPECIFIER = "und:ci"
_BINARY_SPECIFIER = "binary"


def rewrite_collate_specifier(bq_sql: str) -> str:
    """Pre-translate BigQuery SQL for the ``COLLATE(value, specifier)`` form.

    Returns the input unchanged when no ``COLLATE`` call is present.
    Parse failures are tolerated: we return the original SQL so the
    downstream SQLGlot transpile surfaces its own parse error message.
    """
    if "COLLATE" not in bq_sql.upper():
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = _rewrite_collate_nodes(parsed)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _rewrite_collate_nodes(tree: exp.Expression) -> bool:
    """Replace every supported ``Collate`` with the closest DuckDB form.

    Iterates a snapshot list so the replacement nodes don't loop the
    walk. Returns ``True`` when at least one node was rewritten.
    """
    modified = False
    for node in list(tree.find_all(exp.Collate)):
        replacement = _build_replacement(node)
        if replacement is None:
            continue
        node.replace(replacement)
        modified = True
    return modified


def _build_replacement(node: exp.Collate) -> exp.Expression | None:
    """Return the DuckDB-side rewrite for a ``Collate`` call, or ``None`` to skip.

    Skips when the specifier is not a string literal (column-bound
    specifiers don't appear in the corpus and would need a richer
    runtime mapping) or when the specifier value is one we don't
    recognise (the BigQuery service accepts many ICU codes; the
    corpus exercises only ``und:ci`` and ``binary``).
    """
    value = node.this
    specifier = node.expression
    if value is None or not isinstance(specifier, exp.Literal) or not specifier.is_string:
        return None
    spec_value = str(specifier.this)
    if spec_value == _CASE_INSENSITIVE_SPECIFIER:
        return exp.Lower(this=value.copy())
    if spec_value == _BINARY_SPECIFIER:
        return exp.Anonymous(
            this="error",
            expressions=[exp.Literal.string(_BINARY_ERROR_MESSAGE)],
        )
    return None


__all__ = ["rewrite_collate_specifier"]
