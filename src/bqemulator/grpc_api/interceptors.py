"""gRPC server interceptors: correlation, logging, metrics, error translation.

These interceptors are the gRPC analog of the REST middleware stack.
They ensure every RPC gets structured logs, metrics, and a correlation id
(pulled from ``x-correlation-id`` metadata or generated).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import grpc
from grpc.aio import ServerInterceptor

from bqemulator.domain.errors import DomainError, InternalError
from bqemulator.observability.logging_ import (
    bind_correlation_id,
    clear_correlation_id,
    get_logger,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Awaitable, Callable

    from bqemulator.observability.metrics import MetricsRegistry

_log = get_logger(__name__)


class CorrelationInterceptor(ServerInterceptor):
    """Bind a correlation id from metadata (or generate one) for every RPC."""

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        """Bind a correlation id around ``continuation``."""
        cid: str | None = None
        for key, value in handler_call_details.invocation_metadata or ():
            if key.lower() in {"x-correlation-id", "x-request-id"}:
                cid = value
                break
        if cid is None:
            cid = uuid4().hex
        bind_correlation_id(cid)
        try:
            return await continuation(handler_call_details)
        finally:
            clear_correlation_id()


class LoggingInterceptor(ServerInterceptor):
    """Emit structured start/end logs around every RPC."""

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        """Emit a structured log line around each RPC."""
        started = time.perf_counter()
        method = handler_call_details.method
        try:
            result = await continuation(handler_call_details)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            _log.error(
                "grpc.request.failed",
                method=method,
                elapsed_ms=round(elapsed_ms, 2),
                error=str(exc),
            )
            raise
        else:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            _log.info(
                "grpc.request",
                method=method,
                elapsed_ms=round(elapsed_ms, 2),
            )
            return result


class MetricsInterceptor(ServerInterceptor):
    """Record Prometheus counters/histograms for every RPC."""

    def __init__(self, metrics: MetricsRegistry) -> None:
        self._metrics = metrics

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        """Record Prometheus counters and histograms around each RPC."""
        started = time.perf_counter()
        service, method = _split_method(handler_call_details.method)
        status_name = "OK"
        try:
            result = await continuation(handler_call_details)
        except grpc.RpcError as exc:
            status_name = exc.code().name if hasattr(exc, "code") else "UNKNOWN"
            raise
        except DomainError as exc:
            status_name = exc.grpc_status_name
            raise
        except Exception:
            status_name = "INTERNAL"
            raise
        finally:
            elapsed = time.perf_counter() - started
            self._metrics.grpc_requests_total.labels(
                service=service,
                method=method,
                status=status_name,
            ).inc()
            self._metrics.grpc_request_latency_seconds.labels(
                service=service,
                method=method,
            ).observe(elapsed)
        return result


class DomainErrorTranslationInterceptor(ServerInterceptor):
    """Translate :class:`DomainError` into gRPC status codes and abort cleanly."""

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        """Wrap the returned handler so raised ``DomainError``s become aborts."""
        handler = await continuation(handler_call_details)
        return _wrap_handler(handler)


def _wrap_handler(handler: grpc.RpcMethodHandler) -> grpc.RpcMethodHandler:
    """Wrap a handler so raised :class:`DomainError`s become gRPC aborts.

    Handles sync and async unary-unary handlers. Streaming handlers
    (server-streaming ``ReadRows``, bidi ``AppendRows``) are returned
    unchanged by design: they translate errors in-band via
    ``context.set_code`` / ``context.set_details`` or by emitting an
    ``AppendRowsResponse.error`` to keep the stream open. Rewriting the
    generator here would double-report failures and hide the streaming
    error-response contract the storage-API clients expect.
    """
    import asyncio

    if handler.unary_unary is not None:
        original = handler.unary_unary

        async def wrapped_unary_unary(request: Any, context: grpc.ServicerContext) -> Any:
            """Delegate to the original handler, translating DomainError to abort."""
            try:
                result = original(request, context)
                return await result if asyncio.iscoroutine(result) else result
            except DomainError as exc:
                code = getattr(grpc.StatusCode, exc.grpc_status_name, grpc.StatusCode.INTERNAL)
                await context.abort(code, exc.message)
                raise InternalError("unreachable") from exc  # abort raises

        return grpc.unary_unary_rpc_method_handler(
            wrapped_unary_unary,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    return handler


_EXPECTED_METHOD_PARTS = 2


def _split_method(full: str) -> tuple[str, str]:
    """Split ``/package.Service/Method`` into ``(service, method)``."""
    parts = full.strip("/").split("/")
    if len(parts) == _EXPECTED_METHOD_PARTS:
        return parts[0], parts[1]
    return full, ""


__all__ = [
    "CorrelationInterceptor",
    "DomainErrorTranslationInterceptor",
    "LoggingInterceptor",
    "MetricsInterceptor",
]
