"""Control-flow signals and script-level exception wrapping.

Non-local transfer inside the interpreter uses ordinary Python
exceptions:

- :class:`BreakSignal` — caught by the nearest enclosing loop.
- :class:`ContinueSignal` — caught by the nearest enclosing loop.
- :class:`ReturnSignal` — caught by the procedure-call frame.
- :class:`ScriptRaise` — wraps a ``DomainError`` that a handler may
  catch with ``EXCEPTION WHEN ERROR THEN``.

Because each signal is an exception, the interpreter relies on Python's
stack unwinding to reach the correct handler, without any bespoke
trampoline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bqemulator.domain.errors import DomainError


class _ControlSignal(Exception):  # noqa: N818 — signals aren't errors
    """Internal base for control-flow signals."""


class BreakSignal(_ControlSignal):
    """Raised by ``BREAK`` / ``LEAVE`` to exit the nearest loop."""


class ContinueSignal(_ControlSignal):
    """Raised by ``CONTINUE`` / ``ITERATE`` to skip to the next iteration."""


class ReturnSignal(_ControlSignal):
    """Raised by ``RETURN`` to exit the current procedure."""

    def __init__(self, value: Any = None) -> None:
        super().__init__("RETURN")
        self.value = value


class ScriptRaise(Exception):  # noqa: N818 — not an error per se, a wrapper
    """Wraps a ``DomainError`` for scripting's exception-handler layer."""

    def __init__(self, error: DomainError, *, message_override: str | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.message_override = message_override

    @property
    def message(self) -> str:
        """The user-visible message (prefers override over wrapped error)."""
        return self.message_override or self.error.message


__all__ = [
    "BreakSignal",
    "ContinueSignal",
    "ReturnSignal",
    "ScriptRaise",
    "_ControlSignal",
]
