"""TranslationRule abstract base class.

Every BigQuery-specific SQL function or construct that requires custom
translation (beyond what SQLGlot handles natively) is implemented as a
subclass of :class:`TranslationRule`.

Rules follow the **strategy pattern**: each rule knows which AST nodes
it applies to and how to rewrite them. The translator iterates over the
AST and applies every matching rule.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlglot import exp


class TranslationRule(ABC):
    """Base class for BigQuery → DuckDB translation rules.

    Subclasses must implement :meth:`applies_to` and :meth:`rewrite`.
    They should be decorated with ``@register`` from
    :mod:`bqemulator.sql.rules` to join the global registry.

    Example::

        @register
        class SafeDivideRule(TranslationRule):
            name = "SAFE_DIVIDE"

            def applies_to(self, node: exp.Expression) -> bool:
                return isinstance(node, exp.Anonymous) and node.this.upper() == "SAFE_DIVIDE"

            def rewrite(self, node: exp.Expression) -> exp.Expression:
                a, b = node.expressions
                return exp.If(
                    this=exp.EQ(this=b.copy(), expression=exp.Literal.number(0)),
                    true=exp.Null(),
                    false=exp.Div(this=a.copy(), expression=b.copy()),
                )
    """

    #: Human-readable name for logging and the function-mapping docs.
    name: str = ""

    @abstractmethod
    def applies_to(self, node: exp.Expression) -> bool:
        """Return ``True`` if this rule should rewrite ``node``."""
        ...

    @abstractmethod
    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Return a new AST node that replaces ``node``.

        Must NOT mutate ``node`` in place — return a fresh subtree.
        """
        ...


__all__ = ["TranslationRule"]
