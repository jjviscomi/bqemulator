"""Translation rules for BigQuery SAFE_* math functions.

SQLGlot handles these natively when transpiling BigQuery → DuckDB:

- ``SAFE_CAST(x AS T)`` → ``TRY_CAST(x AS T)`` (handled by SQLGlot)
- ``SAFE_DIVIDE(a, b)`` → ``CASE WHEN b <> 0 THEN a / b END``
  (handled by SQLGlot)

The following are recognised by SQLGlot's parser as typed expression
nodes but are NOT transpiled away — we handle them here by wrapping the
underlying arithmetic in DuckDB's ``TRY(...)`` so an overflow surfaces
as ``NULL`` instead of an ``OutOfRangeException``:

- ``SAFE_ADD(a, b)``      → ``TRY(a + b)``
- ``SAFE_SUBTRACT(a, b)`` → ``TRY(a - b)``
- ``SAFE_MULTIPLY(a, b)`` → ``TRY(a * b)``
- ``SAFE_NEGATE(a)``      → ``TRY(0 - a)``

``TRY(0 - a)`` is used instead of ``TRY(-a)`` for SAFE_NEGATE because
DuckDB silently promotes ``-(-INT64_MIN)`` to ``HUGEINT`` (returning
``9223372036854775808`` rather than raising), whereas ``0 - INT64_MIN``
overflows ``BIGINT`` arithmetic — which ``TRY`` then converts to
``NULL``, matching BigQuery's ``SAFE_NEGATE(-9223372036854775808) =
NULL`` semantics.

The same ``TRY(a OP b)`` pattern is correct for ``FLOAT64`` and
``NUMERIC`` inputs: IEEE-754 arithmetic doesn't raise on overflow (it
yields ±Inf), so ``TRY`` is a no-op; DECIMAL overflow that DuckDB
raises is caught by ``TRY`` and surfaces as ``NULL`` — same as
BigQuery.
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule


def _try_wrap(inner: exp.Expression) -> exp.Try:
    """Wrap *inner* in a DuckDB ``TRY(...)`` so overflow returns NULL."""
    return exp.Try(this=inner)


@register
class SafeAddRule(TranslationRule):
    """``SAFE_ADD(a, b)`` → ``TRY(a + b)``.

    SQLGlot parses ``SAFE_ADD`` into ``exp.SafeAdd(this=a, expression=b)``
    and does not transpile it to a DuckDB equivalent. Wrapping in
    ``TRY`` makes BIGINT overflow surface as ``NULL`` instead of an
    ``OutOfRangeException``.
    """

    name = "SAFE_ADD"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.SafeAdd`` nodes."""
        return isinstance(node, exp.SafeAdd)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace ``SAFE_ADD(a, b)`` with ``TRY(a + b)``."""
        return _try_wrap(
            exp.Add(this=node.this.copy(), expression=node.expression.copy()),
        )


@register
class SafeSubtractRule(TranslationRule):
    """``SAFE_SUBTRACT(a, b)`` → ``TRY(a - b)``."""

    name = "SAFE_SUBTRACT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.SafeSubtract`` nodes."""
        return isinstance(node, exp.SafeSubtract)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace ``SAFE_SUBTRACT(a, b)`` with ``TRY(a - b)``."""
        return _try_wrap(
            exp.Sub(this=node.this.copy(), expression=node.expression.copy()),
        )


@register
class SafeMultiplyRule(TranslationRule):
    """``SAFE_MULTIPLY(a, b)`` → ``TRY(a * b)``."""

    name = "SAFE_MULTIPLY"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.SafeMultiply`` nodes."""
        return isinstance(node, exp.SafeMultiply)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace ``SAFE_MULTIPLY(a, b)`` with ``TRY(a * b)``."""
        return _try_wrap(
            exp.Mul(this=node.this.copy(), expression=node.expression.copy()),
        )


@register
class SafeNegateRule(TranslationRule):
    """``SAFE_NEGATE(a)`` → ``TRY(0 - a)``.

    SQLGlot parses ``SAFE_NEGATE(x)`` into ``exp.SafeNegate(this=x)``
    but does not transpile it for the DuckDB target. We rewrite to
    ``TRY(0 - a)`` rather than ``-(a)`` so the BIGINT overflow case
    (``SAFE_NEGATE(INT64_MIN)``) surfaces as ``NULL`` — DuckDB silently
    auto-promotes ``-INT64_MIN`` to ``HUGEINT``, so ``TRY(-a)`` would
    return ``9223372036854775808`` instead of ``NULL``. The
    ``0 - INT64_MIN`` form overflows BIGINT cleanly and ``TRY`` then
    converts the overflow to ``NULL`` as BigQuery does.
    """

    name = "SAFE_NEGATE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.SafeNegate`` nodes."""
        return isinstance(node, exp.SafeNegate)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace ``SAFE_NEGATE(a)`` with ``TRY(0 - a)``."""
        return _try_wrap(
            exp.Sub(this=exp.Literal.number(0), expression=node.this.copy()),
        )
