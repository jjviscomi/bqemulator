"""Translation rules for BigQuery reciprocal / hyperbolic trig functions.

BigQuery exposes the full reciprocal-trig family (``COTH``, ``CSC``,
``CSCH``, ``SEC``, ``SECH``). DuckDB only ships ``SIN`` / ``COS`` /
``TAN`` / ``SINH`` / ``COSH`` / ``TANH`` / ``COT`` natively, so we
rewrite each of the missing functions into its reciprocal-of-the-
primary-trig equivalent:

- ``COTH(x)`` → ``1.0 / TANH(x)``
- ``CSC(x)``  → ``1.0 / SIN(x)``
- ``CSCH(x)`` → ``1.0 / SINH(x)``
- ``SEC(x)``  → ``1.0 / COS(x)``
- ``SECH(x)`` → ``1.0 / COSH(x)``

These rewrites match BigQuery's documented semantics — the reciprocal-
trig family is mathematically defined as the inverse of the primary
trig functions, so the FP64 result is identical bit-for-bit on any
IEEE-754 compliant runtime. The recorded conformance baselines from
real BigQuery confirm exact float-equality with this rewrite when
projected through ``ROUND(_, 6)``.

SQLGlot parses each of these forms into a typed expression node
(``Csc`` / ``Coth`` / etc.) with the single argument stored on
``.this``; we match the typed node and emit the reciprocal expression
directly. ``COT`` is intentionally NOT rewritten — DuckDB ships
``cot(x)`` natively and our existing conformance fixture
(``math_cot``) confirms parity.
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule


def _reciprocal(inner_func_name: str, arg: exp.Expression) -> exp.Div:
    """Build ``1.0 / <inner_func>(arg)`` as a DuckDB expression."""
    return exp.Div(
        this=exp.Literal.number("1.0"),
        expression=exp.Anonymous(this=inner_func_name, expressions=[arg.copy()]),
    )


@register
class CothRule(TranslationRule):
    """``COTH(x)`` → ``1.0 / TANH(x)`` (DuckDB has no native ``COTH``)."""

    name = "COTH"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``Coth`` node."""
        return type(node).__name__ == "Coth"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``1.0 / TANH(arg)``."""
        return _reciprocal("TANH", node.this)


@register
class CscRule(TranslationRule):
    """``CSC(x)`` → ``1.0 / SIN(x)`` (DuckDB has no native ``CSC``)."""

    name = "CSC"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``Csc`` node."""
        return type(node).__name__ == "Csc"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``1.0 / SIN(arg)``."""
        return _reciprocal("SIN", node.this)


@register
class CschRule(TranslationRule):
    """``CSCH(x)`` → ``1.0 / SINH(x)`` (DuckDB has no native ``CSCH``)."""

    name = "CSCH"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``Csch`` node."""
        return type(node).__name__ == "Csch"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``1.0 / SINH(arg)``."""
        return _reciprocal("SINH", node.this)


@register
class SecRule(TranslationRule):
    """``SEC(x)`` → ``1.0 / COS(x)`` (DuckDB has no native ``SEC``)."""

    name = "SEC"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``Sec`` node."""
        return type(node).__name__ == "Sec"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``1.0 / COS(arg)``."""
        return _reciprocal("COS", node.this)


@register
class SechRule(TranslationRule):
    """``SECH(x)`` → ``1.0 / COSH(x)`` (DuckDB has no native ``SECH``)."""

    name = "SECH"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``Sech`` node."""
        return type(node).__name__ == "Sech"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``1.0 / COSH(arg)``."""
        return _reciprocal("COSH", node.this)
