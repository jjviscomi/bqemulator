"""Result type — explicit success/failure for expected domain outcomes.

Exceptions are reserved for *unexpected* failures. For expected outcomes
(SQL parse error, catalog miss, validation failure), use :class:`Result`.

Example::

    def translate(sql: str) -> Result[str, InvalidQueryError]:
        try:
            return Ok(_translate(sql))
        except sqlglot.errors.ParseError as exc:
            return Err(InvalidQueryError(str(exc)))


    match translate(user_sql):
        case Ok(duckdb_sql):
            ...
        case Err(error):
            ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, final

from bqemulator.domain.errors import DomainError

T = TypeVar("T")
E = TypeVar("E", bound=DomainError)
U = TypeVar("U")


@final
@dataclass(slots=True, frozen=True)
class Ok(Generic[T]):
    """Success case of :class:`Result`."""

    value: T

    def is_ok(self) -> bool:
        """Return ``True``."""
        return True

    def is_err(self) -> bool:
        """Return ``False``."""
        return False

    def unwrap(self) -> T:
        """Return the contained value."""
        return self.value

    def map(self, fn: object) -> Ok[object]:
        """Apply ``fn`` to the contained value, returning a new ``Ok``."""
        return Ok(fn(self.value))  # type: ignore[operator]


@final
@dataclass(slots=True, frozen=True)
class Err(Generic[E]):
    """Failure case of :class:`Result`."""

    error: E

    def is_ok(self) -> bool:
        """Return ``False``."""
        return False

    def is_err(self) -> bool:
        """Return ``True``."""
        return True

    def unwrap(self) -> object:
        """Raise the contained :class:`DomainError`.

        Named ``unwrap`` (rather than ``raise``) to mirror the Ok variant.
        """
        raise self.error

    def map(self, fn: object) -> Err[E]:  # noqa: ARG002
        """Return ``self`` — mapping is a no-op on the failure branch."""
        return self


Result = Ok[T] | Err[E]
"""A disjoint union of :class:`Ok` and :class:`Err`.

Use with Python 3.11+ ``match`` statements for exhaustive handling.
"""


__all__ = ["Err", "Ok", "Result"]
