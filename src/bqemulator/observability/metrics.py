"""Prometheus metrics.

Metrics are declared in :class:`MetricsRegistry`. The registry is created
by the composition root and injected into subsystems that record metrics.
The FastAPI ``/metrics`` endpoint is served by :func:`metrics_router`.

Naming follows the Prometheus conventions (snake_case, ``_total`` suffix on
counters, ``_seconds`` for latencies).
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class MetricsRegistry:
    """Container for every metric the emulator emits.

    Using a registry object (rather than module-level globals) lets tests
    create isolated registries per test without polluting the default one.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()

        # HTTP / REST
        self.rest_requests_total = Counter(
            "bqemulator_rest_requests_total",
            "Total REST requests by method, route template, and status class.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.rest_request_latency_seconds = Histogram(
            "bqemulator_rest_request_latency_seconds",
            "REST request latency.",
            ("method", "route"),
            registry=self.registry,
        )

        # gRPC
        self.grpc_requests_total = Counter(
            "bqemulator_grpc_requests_total",
            "Total gRPC requests by service, method, and canonical status.",
            ("service", "method", "status"),
            registry=self.registry,
        )
        self.grpc_request_latency_seconds = Histogram(
            "bqemulator_grpc_request_latency_seconds",
            "gRPC request latency (for unary) or first-response latency (for streaming).",
            ("service", "method"),
            registry=self.registry,
        )

        # Jobs
        self.jobs_total = Counter(
            "bqemulator_jobs_total",
            "Total jobs by type and final status.",
            ("type", "status"),
            registry=self.registry,
        )
        self.job_duration_seconds = Histogram(
            "bqemulator_job_duration_seconds",
            "Job execution time.",
            ("type",),
            registry=self.registry,
        )

        # SQL translation
        self.sql_translation_total = Counter(
            "bqemulator_sql_translation_total",
            "SQL translations attempted, labeled by outcome.",
            ("outcome",),  # ok | parse_error | unsupported
            registry=self.registry,
        )

        # Storage APIs
        self.read_streams_active = Gauge(
            "bqemulator_read_streams_active",
            "Number of active Storage Read API streams.",
            registry=self.registry,
        )
        self.write_streams_active = Gauge(
            "bqemulator_write_streams_active",
            "Number of active Storage Write API streams.",
            ("stream_type",),
            registry=self.registry,
        )

        # Query cache
        self.query_cache_hits_total = Counter(
            "bqemulator_query_cache_hits_total",
            "Query result cache hits.",
            registry=self.registry,
        )
        self.query_cache_misses_total = Counter(
            "bqemulator_query_cache_misses_total",
            "Query result cache misses.",
            registry=self.registry,
        )

        # Build info
        self.build_info = Gauge(
            "bqemulator_build_info",
            "Static build information (value always 1). Labels carry version.",
            ("version",),
            registry=self.registry,
        )


def metrics_router(registry: MetricsRegistry) -> APIRouter:
    """Return a FastAPI router exposing ``/metrics`` for the given registry."""
    router = APIRouter()

    @router.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        payload = generate_latest(registry.registry)
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    return router


__all__ = ["MetricsRegistry", "metrics_router"]
