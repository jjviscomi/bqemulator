"""Tests for structured logging and correlation id propagation."""

from __future__ import annotations

import pytest

from bqemulator.config import LogFormat, LogLevel
from bqemulator.observability.logging_ import (
    bind_correlation_id,
    clear_correlation_id,
    configure_logging,
    get_logger,
)

pytestmark = pytest.mark.unit


class TestCorrelationContextVar:
    def test_bind_and_clear(self) -> None:
        bind_correlation_id("cid-1")
        # The contextvar is internal; we just exercise the API.
        clear_correlation_id()

    def test_can_rebind(self) -> None:
        bind_correlation_id("cid-1")
        bind_correlation_id("cid-2")
        clear_correlation_id()


class TestConfigureLogging:
    def test_json_format_does_not_raise(self) -> None:
        configure_logging(level=LogLevel.INFO, fmt=LogFormat.JSON)

    def test_console_format_does_not_raise(self) -> None:
        configure_logging(level=LogLevel.DEBUG, fmt=LogFormat.CONSOLE)

    def test_all_levels_accepted(self) -> None:
        for level in LogLevel:
            configure_logging(level=level, fmt=LogFormat.JSON)

    def test_reconfigure_is_safe(self) -> None:
        configure_logging(level=LogLevel.INFO, fmt=LogFormat.JSON)
        configure_logging(level=LogLevel.WARNING, fmt=LogFormat.CONSOLE)


class TestGetLogger:
    def test_returns_a_usable_logger(self) -> None:
        configure_logging(level=LogLevel.INFO, fmt=LogFormat.JSON)
        log = get_logger("tests.unit.observability")
        log.info("hello", key="value")  # no raise
