"""Tests for the Prometheus metrics registry."""

from __future__ import annotations

from prometheus_client import CollectorRegistry
import pytest

from bqemulator.observability.metrics import MetricsRegistry

pytestmark = pytest.mark.unit


class TestMetricsRegistry:
    def test_default_registry_is_isolated(self) -> None:
        a = MetricsRegistry()
        b = MetricsRegistry()
        # Different registry objects — isolated (important for tests).
        assert a.registry is not b.registry

    def test_accepts_custom_registry(self) -> None:
        reg = CollectorRegistry()
        m = MetricsRegistry(reg)
        assert m.registry is reg

    def test_rest_requests_counter_labels(self) -> None:
        m = MetricsRegistry()
        m.rest_requests_total.labels(method="GET", route="/healthz", status="2xx").inc()
        # No exception means labels are correctly declared.

    def test_jobs_counter_labels(self) -> None:
        m = MetricsRegistry()
        m.jobs_total.labels(type="QUERY", status="DONE").inc()

    def test_build_info_sets_value_1(self) -> None:
        m = MetricsRegistry()
        m.build_info.labels(version="0.1.0").set(1)

    def test_sql_translation_counter(self) -> None:
        m = MetricsRegistry()
        m.sql_translation_total.labels(outcome="ok").inc()
        m.sql_translation_total.labels(outcome="parse_error").inc()
        m.sql_translation_total.labels(outcome="unsupported").inc()

    def test_read_streams_gauge(self) -> None:
        m = MetricsRegistry()
        m.read_streams_active.inc()
        m.read_streams_active.dec()
        m.read_streams_active.set(5)

    def test_write_streams_gauge_per_stream_type(self) -> None:
        m = MetricsRegistry()
        for stream_type in ("DEFAULT", "COMMITTED", "PENDING", "BUFFERED"):
            m.write_streams_active.labels(stream_type=stream_type).inc()
