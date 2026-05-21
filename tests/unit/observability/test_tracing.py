"""Tests for the OpenTelemetry tracing hookup.

We don't stand up a real OTLP collector — we only verify the config
switch is honored. The enabled-with-endpoint path is exercised in e2e
tests where an OTLP collector is available.
"""

from __future__ import annotations

import pytest

from bqemulator.config import Settings
from bqemulator.observability.tracing import configure_tracing

pytestmark = pytest.mark.unit


def test_configure_tracing_is_noop_when_disabled() -> None:
    settings = Settings(tracing_enabled=False, otlp_endpoint=None)
    configure_tracing(settings)  # no raise


def test_configure_tracing_is_noop_when_endpoint_missing() -> None:
    settings = Settings(tracing_enabled=True, otlp_endpoint=None)
    configure_tracing(settings)  # no raise — early return
