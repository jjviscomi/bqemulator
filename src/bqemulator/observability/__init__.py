"""Observability primitives.

Exposes three facets:

* :mod:`bqemulator.observability.logging_` — structlog configuration
* :mod:`bqemulator.observability.metrics` — Prometheus collectors + registry
* :mod:`bqemulator.observability.tracing` — OpenTelemetry setup
"""

from __future__ import annotations

from bqemulator.observability.logging_ import (
    bind_correlation_id,
    clear_correlation_id,
    configure_logging,
    get_logger,
)
from bqemulator.observability.metrics import MetricsRegistry, metrics_router
from bqemulator.observability.tracing import configure_tracing

__all__ = [
    "MetricsRegistry",
    "bind_correlation_id",
    "clear_correlation_id",
    "configure_logging",
    "configure_tracing",
    "get_logger",
    "metrics_router",
]
