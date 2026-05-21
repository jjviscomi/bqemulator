"""OpenTelemetry tracing setup.

Tracing is opt-in: it only runs when ``settings.tracing_enabled`` is true
and an ``otlp_endpoint`` is configured. In the default case, the OTel API
is a no-op and has zero overhead.
"""

from __future__ import annotations

from bqemulator.config import Settings
from bqemulator.observability.logging_ import get_logger

_log = get_logger(__name__)


def configure_tracing(settings: Settings) -> None:
    """Configure OpenTelemetry tracing according to ``settings``.

    No-ops if tracing is disabled or no OTLP endpoint is set. Idempotent.
    """
    if not settings.tracing_enabled or settings.otlp_endpoint is None:
        _log.debug("tracing.disabled", reason="tracing_enabled=False or otlp_endpoint=None")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover
        _log.warning("tracing.import_failed")
        return

    resource = Resource.create(
        {
            "service.name": "bqemulator",
            "service.namespace": "bqemulator",
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _log.info("tracing.enabled", endpoint=settings.otlp_endpoint)


__all__ = ["configure_tracing"]
