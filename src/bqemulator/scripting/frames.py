"""Lexical scoping for BigQuery scripts.

Each BEGIN/END block pushes a :class:`Frame`. ``DECLARE`` binds the
name in the current frame; ``SET`` walks outward to find the first
frame that owns the name. Procedure calls open a fresh frame with
only the parameter bindings — they do not see the caller's locals.

All types are currently stored as their BigQuery type-kind strings
(e.g. ``INT64``, ``STRING``). A future phase may widen to full
``StandardSqlDataType`` dicts for struct/array precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bqemulator.domain.errors import InvalidQueryError


@dataclass(slots=True)
class Variable:
    """A single script variable."""

    name: str
    type_name: str
    value: Any = None


@dataclass(slots=True)
class Frame:
    """A lexical-scope frame."""

    kind: str  # "root" | "block" | "procedure" | "loop"
    variables: dict[str, Variable] = field(default_factory=dict)


class FrameStack:
    """Stack of lexical scopes.

    Usage::

        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 1)
        stack.set("x", 2)
        value = stack.lookup("x")
        stack.pop()
    """

    def __init__(self) -> None:
        self._frames: list[Frame] = []

    def push(self, kind: str = "block") -> None:
        """Open a new frame."""
        self._frames.append(Frame(kind=kind))

    def pop(self) -> Frame:
        """Discard the top frame and return it."""
        if not self._frames:
            raise InvalidQueryError("Internal error: FrameStack underflow")
        return self._frames.pop()

    @property
    def depth(self) -> int:
        """Number of active frames."""
        return len(self._frames)

    def declare(self, name: str, type_name: str, value: Any = None) -> None:
        """Bind ``name`` in the current frame.

        Raises :class:`InvalidQueryError` if the name already exists in
        the *current* frame — shadowing an outer frame is allowed and
        matches BigQuery's block-scoping semantics.
        """
        if not self._frames:
            raise InvalidQueryError("Internal error: declare without a frame")
        current = self._frames[-1]
        if name in current.variables:
            raise InvalidQueryError(f"Variable {name!r} already declared in this scope")
        current.variables[name] = Variable(name=name, type_name=type_name, value=value)

    def set(self, name: str, value: Any) -> None:
        """Assign to an existing variable.

        Walks outward to find the first frame that owns the name.
        Raises :class:`InvalidQueryError` if no frame owns it.
        """
        for frame in reversed(self._frames):
            if name in frame.variables:
                frame.variables[name].value = value
                return
        raise InvalidQueryError(f"Unknown variable: {name}")

    def lookup(self, name: str) -> Any:
        """Return the value of a script variable."""
        for frame in reversed(self._frames):
            if name in frame.variables:
                return frame.variables[name].value
        raise InvalidQueryError(f"Unknown variable: {name}")

    def has(self, name: str) -> bool:
        """Return whether a variable is visible from the current scope."""
        return any(name in f.variables for f in reversed(self._frames))

    def all_visible(self) -> dict[str, Variable]:
        """Return every variable visible from the current scope.

        Variables in inner frames shadow outer frames.
        """
        return {name: var for frame in self._frames for name, var in frame.variables.items()}

    def snapshot_current(self) -> dict[str, Any]:
        """Return a ``name → value`` snapshot of every visible variable.

        Used at procedure exit to propagate OUT / INOUT parameter writes
        back to the caller. Inner frames shadow outer frames; the
        returned mapping carries the value the procedure body left in
        each name.
        """
        out: dict[str, Any] = {}
        for frame in self._frames:
            for name, var in frame.variables.items():
                out[name] = var.value
        return out


__all__ = ["Frame", "FrameStack", "Variable"]
