"""Pre-translator for ``UNNEST(array<STRUCT>)`` with mixed positional structs.

BigQuery's array-of-STRUCT semantics state that when an array literal
contains structs and the *first* struct has named fields, all
subsequent structs inherit those names by position. So:

    UNNEST([
        STRUCT('a' AS label, 1 AS value),
        STRUCT('b', 2),
        STRUCT('c', 3),
    ])

surfaces three rows with columns ``label`` / ``value``. The user can
then bind those names directly in the enclosing SELECT.

The downstream :func:`rewrite_struct_helpers` pass converts every
unaliased positional ``STRUCT(value, value, …)`` to DuckDB's
``ROW(…)`` so INSERT VALUES / UNION ALL targets align positionally
with their declared schema. That rewrite, however, *breaks*
UNNEST-of-array-of-structs:

    Before: UNNEST([STRUCT('a' AS label, …), STRUCT('b', 2), …])
    After:  UNNEST([STRUCT('a' AS label, …), ROW('b', 2), …])

SQLGlot then transpiles the array's first element to the named
DuckDB struct literal ``{'label': 'a', 'value': 1}`` but leaves the
``ROW(…)`` elements as positional, producing a mixed-shape array
DuckDB cannot bind by field name. The outer ``SELECT label, value``
fails with ``Binder Error: Referenced column "label" not found in
FROM clause! Candidate bindings: "unnest"``.

This pre-translator runs *before* ``rewrite_struct_helpers`` and
propagates the first struct's field names to every subsequent
positional struct in the same array literal — strictly when the
array is the operand of an ``UNNEST(...)`` call. After the rewrite,
every struct in the array carries explicit ``AS <name>`` aliases, so
``rewrite_struct_helpers`` sees no positional structs to convert,
SQLGlot transpiles the array to a list of named DuckDB struct
literals, and the natural transpile output ``SELECT … FROM (SELECT
UNNEST([…], max_depth => 2))`` destructures the struct into per-field
columns matching the BigQuery contract.

Other UNNEST shapes — bare scalar arrays (``UNNEST([1, 2, 3])``),
arrays of fully-named structs, arrays whose first element is itself
positional — are left untouched: the closure is narrowly targeted at
the mixed-shape case the conformance corpus exercises.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

# Minimum number of struct elements before the first struct's named
# fields can seed siblings. With a single element there is no sibling
# to rename — the rewriter declines to touch the array.
_MIN_STRUCTS_FOR_PROPAGATION = 2


def rewrite_unnest_struct(bq_sql: str) -> str:
    """Propagate named-struct field aliases inside ``UNNEST([...])`` arrays.

    No-op when the input contains no ``UNNEST`` keyword, when SQLGlot
    cannot parse the input, or when no rewrite is required (every
    UNNEST-bound array is already homogeneously named or positional).
    """
    if "UNNEST" not in bq_sql.upper():
        return bq_sql

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — defer to downstream parse error
        return bq_sql

    modified = False
    for unnest in tree.find_all(exp.Unnest):
        if _propagate_struct_field_names(unnest):
            modified = True

    if not modified:
        return bq_sql
    return tree.sql(dialect="bigquery")


def _propagate_struct_field_names(unnest: exp.Unnest) -> bool:
    """For one ``UNNEST(...)`` call, propagate first-struct names.

    Returns ``True`` if at least one positional struct in the
    operand array was rewritten to carry the first struct's field
    aliases.
    """
    expressions = unnest.expressions
    if len(expressions) != 1:
        return False
    array = expressions[0]
    if not isinstance(array, exp.Array):
        return False

    structs = list(array.expressions)
    if len(structs) < _MIN_STRUCTS_FOR_PROPAGATION:
        # A single struct can't seed names from earlier siblings; no-op.
        return False
    if not all(isinstance(s, exp.Struct) for s in structs):
        # Mixed shapes (e.g. an array of arrays) are out of scope.
        return False

    first = structs[0]
    field_names = _struct_field_names(first)
    if field_names is None:
        # First element is positional → BigQuery treats every element as
        # positional (no name inheritance) so we leave the array alone.
        return False

    modified = False
    for struct in structs[1:]:
        if _maybe_rename_struct_fields(struct, field_names):
            modified = True
    return modified


def _struct_field_names(struct: exp.Struct) -> tuple[str, ...] | None:
    """Return the named-field tuple for ``struct``, or ``None`` if positional.

    A struct counts as named iff every child is a ``PropertyEQ`` whose
    LHS is an ``Identifier`` — the ``STRUCT(value AS name)`` shape.
    Mixed children (some aliased, some not) fall through to the
    positional treatment so we don't fabricate names for elements the
    user left intentionally anonymous.
    """
    children = list(struct.expressions)
    if not children:
        return None
    names: list[str] = []
    for child in children:
        if not isinstance(child, exp.PropertyEQ):
            return None
        ident = child.this
        if not isinstance(ident, exp.Identifier):
            return None
        names.append(ident.name)
    return tuple(names)


def _maybe_rename_struct_fields(
    struct: exp.Struct,
    field_names: tuple[str, ...],
) -> bool:
    """Wrap every positional child of ``struct`` in ``AS <field_name>``.

    Returns ``True`` if the struct was actually modified. A struct
    that already carries named children is left alone; one whose
    arity does not match ``field_names`` is also left alone so the
    downstream layers can raise the right parse / bind error.
    """
    children = list(struct.expressions)
    if len(children) != len(field_names):
        return False
    if all(isinstance(c, exp.PropertyEQ) for c in children):
        # Already fully named.
        return False
    if any(isinstance(c, exp.PropertyEQ) for c in children):
        # Mixed within a single struct — defer to downstream errors.
        return False

    new_children = [
        exp.PropertyEQ(
            this=exp.to_identifier(name),
            expression=child.copy(),
        )
        for name, child in zip(field_names, children, strict=True)
    ]
    struct.set("expressions", new_children)
    return True


__all__ = ["rewrite_unnest_struct"]
