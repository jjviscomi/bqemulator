"""Clock protocol — injectable time source for deterministic tests.

Production code uses :class:`SystemClock`. Tests inject :class:`FrozenClock`
to make timestamps predictable.

Every timestamp emitted by the emulator (job start/end, table creation, row
insert time) flows through a :class:`Clock`; no code should call
:func:`datetime.now` directly outside of this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Protocol for injectable time sources."""

    def now(self) -> datetime:
        """Return the current UTC datetime."""
        ...

    def now_ms(self) -> int:
        """Return the current UTC epoch milliseconds."""
        ...


class SystemClock:
    """Wall-clock implementation of :class:`Clock`."""

    def now(self) -> datetime:
        """Return current UTC time from the system clock."""
        return datetime.now(tz=UTC)

    def now_ms(self) -> int:
        """Return current UTC epoch milliseconds."""
        return int(self.now().timestamp() * 1000)


@dataclass(slots=True)
class FrozenClock:
    """Test clock that advances only when :meth:`advance` is called.

    Example::

        clock = FrozenClock(datetime(2026, 4, 15, tzinfo=UTC))
        clock.now()  # datetime(2026, 4, 15, 0, 0, tzinfo=UTC)
        clock.advance(seconds=60)
        clock.now()  # datetime(2026, 4, 15, 0, 1, tzinfo=UTC)
    """

    current: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))

    def now(self) -> datetime:
        """Return the frozen current time."""
        return self.current

    def now_ms(self) -> int:
        """Return the frozen current time in epoch milliseconds."""
        return int(self.current.timestamp() * 1000)

    def advance(
        self,
        *,
        seconds: float = 0,
        milliseconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        days: float = 0,
    ) -> None:
        """Advance the clock by the given amount."""
        from datetime import timedelta

        delta = timedelta(
            days=days,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
            milliseconds=milliseconds,
        )
        self.current = self.current + delta


__all__ = ["Clock", "FrozenClock", "SystemClock"]
