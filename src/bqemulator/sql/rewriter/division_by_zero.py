"""Pre-translator rewrite for strict division-by-zero raising (scope-expansion #17).

BigQuery's ``/`` operator raises ``Division by zero`` (OUT_OF_RANGE) when
the right operand evaluates to ``0`` — both for integer ``1 / 0`` and
float ``1.0 / 0.0`` (and ``0 / 0`` raises rather than yielding ``NaN``).
DuckDB returns ``Inf`` / ``NaN`` instead of raising, so the emulator
diverges from BigQuery on every bare-``/`` query that hits a zero
divisor. The user-visible consequence is that a script's
``EXCEPTION WHEN ERROR`` handler never fires for a ``1 / 0`` inside
``EXECUTE IMMEDIATE`` — the fixture
``routines_scripting/script_exception_handler`` exercises exactly that
shape.

The pre-translator walks every :class:`sqlglot.exp.Div` node at the
BigQuery AST level (before SQLGlot's BQ → DuckDB transpile) and
replaces it with::

    CASE WHEN <divisor> = 0
         THEN error('Division by zero: <numerator> / <divisor>')
         ELSE <numerator> / <divisor>
    END

DuckDB's ``error(VARCHAR)`` function raises ``Invalid Input Error: <msg>``
which the storage engine surfaces as an exception, the script
interpreter's ``_run_statement_with_params`` / ``_run_query_with_params``
wraps in :class:`InvalidQueryError`, and the ``BEGIN ... EXCEPTION WHEN
ERROR THEN ... END`` block catches in :meth:`_exec_begin`.

The CASE form is critical — DuckDB's ``IF(cond, then, else)`` macro
evaluates *both* branches eagerly, so ``IF(b=0, error(...), a/b)`` also
evaluates ``a/b`` and yields DuckDB's ``Inf`` rather than reaching the
error branch. ``CASE`` is short-circuited per SQL semantics.

**Negative guards** — the walk leaves a ``Div`` alone when:

* The divisor is a non-zero literal numeric (``a / 2``, ``a / -3.14``).
  The CASE wrap is a no-op in this case, so we save the expansion and
  the AST stays simple — a sizeable optimisation for the common
  divide-by-constant case in user queries.

The Bucket J ``IEEE_DIVIDE`` rule and SQLGlot's native ``SAFE_DIVIDE``
transpile both produce ``Div`` AST nodes after this pre-translator has
already run (``IeeeDivideRule`` runs in the post-translate rule pass;
``SAFE_DIVIDE`` is lowered to ``CASE WHEN denominator <> 0 THEN
numerator / denominator END`` by SQLGlot's BQ → DuckDB transpile). The
pre-translator only sees ``Div`` nodes that the user *wrote* with
``/`` — function-call divides are opaque ``Anonymous`` / typed nodes
when we walk. That separation gives us the negative-guard semantics
without any explicit ancestor checks.

``SAFE.X(...)`` (the function-prefix form) is already rewritten to
``TRY(...)`` by the upstream ``safe_helpers`` pre-translator. A
user-written ``a / b`` inside ``SAFE.X(...)`` therefore lands inside a
``TRY`` after both pre-translators run. We still wrap the ``Div``: the
CASE raises on ``b = 0`` and ``TRY`` catches the raise, yielding
``NULL`` — matching BigQuery's ``SAFE.X(a / 0) = NULL`` semantic. The
alternative (skipping the wrap inside ``TRY``) would leave the bare
``a / b`` returning ``Inf``, which ``TRY`` would pass through unchanged.

Pipeline order: register AFTER ``safe_helpers`` so ``SAFE.X(...)`` is
already a ``TRY`` shell by the time we walk for ``Div``. The walk
snapshots every ``Div`` via :meth:`exp.Expression.find_all` (pre-order
DFS) and iterates the snapshot *in reverse* so a child ``Div`` is
rewritten before its parent — when we wrap an outer ``(a / b) / c``,
its ``this`` already points at the inner CASE so the outer's ELSE
branch receives the already-wrapped form.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def rewrite_division_by_zero(bq_sql: str) -> str:
    """Pre-translate BigQuery SQL to raise on ``a / 0`` for the bare ``/`` operator.

    Returns the input unchanged when no rewrite is needed (the common
    case for queries without a ``/`` operator).

    Parse failures are tolerated: we return the original SQL so the
    downstream SQLGlot transpile surfaces its own parse error message.
    """
    if "/" not in bq_sql:
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = _rewrite_div_nodes(parsed)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _rewrite_div_nodes(tree: exp.Expression) -> bool:
    """Wrap every ``Div`` in ``tree`` with a CASE that raises on zero divisor.

    Iterates the ``find_all`` snapshot in *reverse* so children come
    before parents (find_all is pre-order DFS). That way a nested
    ``(a / b) / c`` rewrites the inner ``a / b`` first; when the outer
    Div is rewritten next, its ``this`` already references the inner
    CASE node and the new outer CASE's ELSE branch carries the
    inner-wrapped shape.

    Returns ``True`` when at least one ``Div`` was rewritten.
    """
    modified = False
    for node in reversed(list(tree.find_all(exp.Div))):
        if node.parent is None and node is not tree:
            # Detached by an earlier replacement (e.g. a Div that lived
            # only inside the original node now replaced). Skip.
            continue
        divisor = node.expression
        numerator = node.this
        if divisor is None or numerator is None:
            continue
        if _is_nonzero_literal(divisor):
            continue
        node.replace(_build_division_case(numerator, divisor))
        modified = True
    return modified


def _is_nonzero_literal(node: exp.Expression) -> bool:
    """Return ``True`` iff *node* is a numeric literal whose value is non-zero.

    Recognises bare ``Literal`` (``2``, ``2.5``) and ``Neg(Literal(...))``
    (``-2``, ``-2.5``) — the two AST shapes SQLGlot uses for signed
    numeric literals in BigQuery. A literal ``0`` / ``0.0`` returns
    ``False`` so the wrap fires and the runtime CASE raises.
    """
    if isinstance(node, exp.Paren):
        inner = node.this
        if inner is None:
            return False
        return _is_nonzero_literal(inner)
    if isinstance(node, exp.Neg):
        inner = node.this
        if inner is None:
            return False
        return _is_nonzero_literal(inner)
    if not isinstance(node, exp.Literal) or node.is_string:
        return False
    try:
        return float(str(node.this)) != 0.0
    except ValueError:
        return False


_DIVISION_BY_ZERO_MESSAGE = "Division by zero"


def _build_division_case(
    numerator: exp.Expression,
    divisor: exp.Expression,
) -> exp.Case:
    """Build ``CASE WHEN divisor = 0 THEN error('Division by zero') ELSE numerator/divisor END``.

    Both operands are copied so the resulting CASE owns its subtree —
    the original ``Div`` node can be safely discarded by
    :meth:`exp.Expression.replace`. The error message is the literal
    BigQuery wording (``Division by zero``) so a query that surfaces
    the error message directly matches BigQuery byte-for-byte; the
    ``EXCEPTION WHEN ERROR`` handler ignores the payload either way.
    """
    error_call = exp.Anonymous(
        this="error",
        expressions=[exp.Literal.string(_DIVISION_BY_ZERO_MESSAGE)],
    )
    condition = exp.EQ(
        this=divisor.copy(),
        expression=exp.Literal.number(0),
    )
    div_branch = exp.Div(
        this=numerator.copy(),
        expression=divisor.copy(),
    )
    return exp.Case(
        ifs=[exp.If(this=condition, true=error_call)],
        default=div_branch,
    )


__all__ = ["rewrite_division_by_zero"]
