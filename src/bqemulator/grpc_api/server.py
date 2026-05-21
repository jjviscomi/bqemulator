"""gRPC server factory.

Hosts the standard health service plus the BigQuery Storage Read API
(Phase 4+) and Storage Write API (Phase 5+).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import grpc
from grpc.aio import Server
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from bqemulator.grpc_api.interceptors import (
    CorrelationInterceptor,
    DomainErrorTranslationInterceptor,
    LoggingInterceptor,
    MetricsInterceptor,
)
from bqemulator.observability.logging_ import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext

_log = get_logger(__name__)


def build_grpc_server(context: AppContext) -> tuple[Server, int]:
    """Construct a configured :class:`grpc.aio.Server` and its bound port.

    Returns ``(server, port)`` — call ``await server.start()`` on it and
    (optionally) ``await server.wait_for_termination()``.
    """
    # gRPC's default max_receive_message_length is 4 MiB — smaller than
    # BigQuery's 10 MiB AppendRows cap. Raise the channel-level limit to
    # slightly above our app-level cap so the servicer (not the
    # transport) surfaces RESOURCE_EXHAUSTED on oversize payloads — that
    # keeps error messages readable and matches real BigQuery semantics.
    app_cap = context.settings.write_api_max_request_bytes
    transport_cap = app_cap + 1024 * 1024  # 1 MiB of headroom
    server = grpc.aio.server(
        interceptors=(
            CorrelationInterceptor(),
            LoggingInterceptor(),
            MetricsInterceptor(context.metrics),
            DomainErrorTranslationInterceptor(),
        ),
        options=[
            ("grpc.max_send_message_length", transport_cap),
            ("grpc.max_receive_message_length", transport_cap),
        ],
    )

    # Standard health service — clients and container orchestrators expect this.
    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    # BigQuery Storage Read API.
    from bqemulator.grpc_api.read_servicer import BigQueryReadHandler

    server.add_generic_rpc_handlers([BigQueryReadHandler(context)])
    _log.info("grpc.storage_read.registered")

    # BigQuery Storage Write API.
    from bqemulator.grpc_api.write_servicer import BigQueryWriteHandler

    server.add_generic_rpc_handlers([BigQueryWriteHandler(context)])
    _log.info("grpc.storage_write.registered")

    bind_addr = f"{context.settings.grpc_host}:{context.settings.grpc_port}"
    port = server.add_insecure_port(bind_addr)
    _log.info("grpc.listen", host=context.settings.grpc_host, port=port)
    return server, port


__all__ = ["build_grpc_server"]
