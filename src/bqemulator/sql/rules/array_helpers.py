"""Translation rules for BigQuery array primitives that DuckDB lacks under the BQ names.

BigQuery's array reference documents several primitives that pass through
SQLGlot's BQ → DuckDB transpile unchanged because DuckDB has no
identically named builtins:

* ``ARRAY_FIRST(arr)`` / ``ARRAY_LAST(arr)`` — return the first / last
  element of an array. **BigQuery raises** when the array is empty
  (``ARRAY_FIRST cannot get the first element of an empty array`` /
  ``ARRAY_LAST cannot get the last element of an empty array``);
  recorded against real BQ 2026-05-18 in
  ``arr_first_empty`` / ``arr_last_empty``. DuckDB's
  :func:`list_extract` returns ``NULL`` on empty input (and on
  out-of-bounds), so a bare rewrite to ``list_extract(arr, 1)``
  diverges from the BigQuery error contract. The rules below emit
  the empty-array check as a ``CASE`` with DuckDB's
  ``error(VARCHAR)`` builtin in the empty branch — same pattern as
  the strict-division-by-zero pre-translator
  (:mod:`bqemulator.sql.rewriter.division_by_zero`).

* ``ARRAY_INCLUDES`` / ``ARRAY_INCLUDES_ANY`` / ``ARRAY_INCLUDES_ALL``
  are documented in the BigQuery reference but the live service
  responds with ``Function ARRAY_INCLUDES[_ANY|_ALL] is not yet
  implemented.`` (probed against real BQ 2026-05-18). Following the
  ``TIMESTAMP_FROM_UNIX_MILLIS`` precedent from top-30 gap-closure
  session #2 (2026-05-18), these three surfaces are omitted from
  :mod:`tests.conformance._surface_inventory` until BigQuery ships
  them. No translator rules are needed for them.

The ``SAFE_ORDINAL(n)`` case is intentionally handled by the upstream
SQLGlot BQ → DuckDB transpile alone — the transpile strips the
``SafeOrdinal`` wrapper down to a bare ``arr[n]`` ``Bracket`` and
DuckDB's bracket indexing already returns ``NULL`` on
out-of-bounds / 0-index (1-indexed semantics matching BigQuery's
``SAFE_ORDINAL``). The ``arr_safe_ordinal_oob`` conformance fixture
pins this contract so a future SQLGlot or DuckDB regression that
changes the bracket semantic surfaces as a test failure.
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule


def _anon(name: str, *args: exp.Expression) -> exp.Anonymous:
    """Build an anonymous DuckDB-side function call with copied args."""
    return exp.Anonymous(this=name, expressions=[arg.copy() for arg in args])


def _build_empty_check_case(
    arr: exp.Expression,
    index: int,
    message: str,
) -> exp.Case:
    """Emit ``CASE WHEN array_length(arr) = 0 THEN error(msg) ELSE list_extract(arr, index) END``.

    The CASE form is required (vs ``IF``) so DuckDB short-circuits past
    the ``error()`` call when the array is non-empty — DuckDB's ``IF``
    macro evaluates both branches eagerly.
    """
    error_call = _anon("error", exp.Literal.string(message))
    empty_condition = exp.EQ(
        this=_anon("array_length", arr.copy()),
        expression=exp.Literal.number(0),
    )
    list_extract_call = _anon(
        "list_extract",
        arr.copy(),
        exp.Literal.number(index),
    )
    return exp.Case(
        ifs=[exp.If(this=empty_condition, true=error_call)],
        default=list_extract_call,
    )


_ARRAY_FIRST_EMPTY_MESSAGE = "ARRAY_FIRST cannot get the first element of an empty array"
_ARRAY_LAST_EMPTY_MESSAGE = "ARRAY_LAST cannot get the last element of an empty array"


@register
class ArrayFirstRule(TranslationRule):
    """``ARRAY_FIRST(arr)`` → empty-check CASE around ``list_extract(arr, 1)``.

    The expansion is ``CASE WHEN array_length(arr) = 0 THEN error(<msg>)
    ELSE list_extract(arr, 1) END`` — see :func:`_build_empty_check_case`
    for the AST construction.

    SQLGlot parses BigQuery's ``ARRAY_FIRST`` into the typed
    :class:`exp.ArrayFirst` node; its DuckDB generator passes the call
    through unchanged so DuckDB raises a ``Scalar Function with name
    array_first does not exist`` Catalog Error without this rule.

    The rewrite preserves BigQuery's "error on empty" contract — see
    the ``arr_first_empty`` conformance fixture recorded against real
    BQ 2026-05-18 for the canonical error wording.
    """

    name = "ARRAY_FIRST"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``ArrayFirst`` AST node."""
        return type(node).__name__ == "ArrayFirst"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit the empty-check CASE wrapping ``list_extract(arr, 1)``."""
        operand = node.this
        if operand is None:
            return node
        return _build_empty_check_case(operand, 1, _ARRAY_FIRST_EMPTY_MESSAGE)


@register
class ArrayLastRule(TranslationRule):
    """``ARRAY_LAST(arr)`` → empty-check CASE around ``list_extract(arr, -1)``.

    Mirror of :class:`ArrayFirstRule` for the last-element form.
    DuckDB's :func:`list_extract` accepts negative indices counting
    from the end (``-1`` is the last element), matching BigQuery's
    documented ``ARRAY_LAST`` semantic for non-empty arrays. The
    empty-array branch raises with the recorded BQ wording so the
    ``arr_last_empty`` conformance fixture's ``message_pattern``
    matches.
    """

    name = "ARRAY_LAST"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``ArrayLast`` AST node."""
        return type(node).__name__ == "ArrayLast"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit the empty-check CASE wrapping ``list_extract(arr, -1)``."""
        operand = node.this
        if operand is None:
            return node
        return _build_empty_check_case(operand, -1, _ARRAY_LAST_EMPTY_MESSAGE)


__all__ = [
    "ArrayFirstRule",
    "ArrayLastRule",
]
