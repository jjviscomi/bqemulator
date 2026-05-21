"""Tests for Clock and FrozenClock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.domain.clock import Clock, FrozenClock, SystemClock

pytestmark = pytest.mark.unit


class TestSystemClock:
    def test_now_returns_utc(self) -> None:
        c = SystemClock()
        assert c.now().tzinfo is UTC

    def test_now_ms_is_close_to_now(self) -> None:
        c = SystemClock()
        ts_from_dt = int(c.now().timestamp() * 1000)
        ts_from_ms = c.now_ms()
        assert abs(ts_from_dt - ts_from_ms) < 5000  # <5s slack


class TestFrozenClock:
    def test_default_year(self) -> None:
        c = FrozenClock()
        assert c.now().year == 2026

    def test_constructor_pins_time(self) -> None:
        pinned = datetime(2030, 3, 5, 14, 30, tzinfo=UTC)
        c = FrozenClock(pinned)
        assert c.now() == pinned

    def test_now_is_stable_until_advance(self) -> None:
        c = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
        first = c.now()
        second = c.now()
        assert first == second

    def test_advance_seconds(self) -> None:
        c = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
        c.advance(seconds=60)
        assert c.now() == datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

    def test_advance_days(self) -> None:
        c = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
        c.advance(days=30)
        assert c.now() == datetime(2026, 1, 31, tzinfo=UTC)

    def test_now_ms_tracks_advance(self) -> None:
        c = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
        start = c.now_ms()
        c.advance(milliseconds=1234)
        assert c.now_ms() - start == 1234


class TestClockProtocol:
    def test_system_clock_satisfies_protocol(self) -> None:
        c: Clock = SystemClock()
        assert c.now() is not None

    def test_frozen_clock_satisfies_protocol(self) -> None:
        c: Clock = FrozenClock()
        assert c.now() is not None
