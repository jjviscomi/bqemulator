"""FastAPI middleware: gzip decoding, correlation IDs, access logs, metrics.

Request flow::

    request
      -> GzipRequestMiddleware (decode Content-Encoding: gzip bodies)
      -> CorrelationIdMiddleware (bind cid to contextvar)
      -> AccessLogMiddleware (emit structured start/end log)
      -> MetricsMiddleware (record rest_requests_total + latency)
      -> handler
"""

from __future__ import annotations

import gzip
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bqemulator.observability.logging_ import (
    bind_correlation_id,
    clear_correlation_id,
    get_logger,
)

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.observability.metrics import MetricsRegistry

_log = get_logger(__name__)

# Header names follow common conventions. The first one found (in order)
# is preferred; if none are present we generate a UUID.
_CORRELATION_HEADERS = (
    "x-correlation-id",
    "x-request-id",
    "x-cloud-trace-context",
)


class GzipRequestMiddleware:
    """Decode ``Content-Encoding: gzip`` request bodies before routing.

    The official Google Cloud Java BigQuery client gzips POST/PUT/PATCH
    bodies above a small threshold; the real BigQuery service decodes
    them transparently. Without this middleware the FastAPI app sees
    raw gzipped bytes and JSON parsing fails with a UnicodeDecodeError.

    Implemented as a pure-ASGI middleware (not BaseHTTPMiddleware) so
    the rewritten body is delivered through the ASGI receive channel
    exactly as if it had arrived uncompressed — the downstream
    BaseHTTPMiddleware chain and Starlette/FastAPI request parsing
    code paths are unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        encoding: bytes | None = None
        for key, value in headers:
            if key.lower() == b"content-encoding":
                encoding = value.lower().strip()
                break

        if encoding is None or encoding == b"identity":
            await self._app(scope, receive, send)
            return

        if encoding != b"gzip":
            # Other encodings (deflate, br) aren't supported. Surface
            # a 415 immediately rather than letting JSON parsing fail.
            response = JSONResponse(
                status_code=415,
                content={
                    "error": {
                        "code": 415,
                        "message": (f"Unsupported Content-Encoding: {encoding.decode('latin-1')}"),
                        "status": "UNSUPPORTED_MEDIA_TYPE",
                    },
                },
            )
            await response(scope, receive, send)
            return

        compressed = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                # Pass disconnects through unchanged.
                await self._app(scope, receive, send)
                return
            compressed.extend(message.get("body", b""))
            more = message.get("more_body", False)

        try:
            decompressed = gzip.decompress(bytes(compressed))
        except OSError as exc:
            response = JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": 400,
                        "message": f"Malformed gzip request body: {exc}",
                        "status": "INVALID_ARGUMENT",
                    },
                },
            )
            await response(scope, receive, send)
            return

        # Rebuild the scope's headers: drop content-encoding, update
        # content-length to the decoded size.
        new_headers: list[tuple[bytes, bytes]] = []
        for key, value in headers:
            lk = key.lower()
            if lk in (b"content-encoding", b"content-length"):
                continue
            new_headers.append((key, value))
        new_headers.append((b"content-length", str(len(decompressed)).encode("ascii")))
        scope = {**scope, "headers": new_headers}

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {
                    "type": "http.request",
                    "body": decompressed,
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        await self._app(scope, replay_receive, send)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Ensure every request has a correlation id, bound to logging context."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        cid: str | None = None
        for name in _CORRELATION_HEADERS:
            value = request.headers.get(name)
            if value:
                cid = value.split("/", 1)[0]  # X-Cloud-Trace-Context has trace/span
                break
        if cid is None:
            cid = uuid4().hex

        bind_correlation_id(cid)
        try:
            response = await call_next(request)
        finally:
            clear_correlation_id()

        response.headers["x-correlation-id"] = cid
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit a structured log line per request with timing and status."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        _log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=round(elapsed_ms, 2),
            client=request.client.host if request.client else None,
        )
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record Prometheus counters and histograms for every REST request."""

    def __init__(self, app: object, metrics: MetricsRegistry) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._metrics = metrics

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started

        route = _route_template(request)
        status_class = f"{response.status_code // 100}xx"

        self._metrics.rest_requests_total.labels(
            method=request.method,
            route=route,
            status=status_class,
        ).inc()
        self._metrics.rest_request_latency_seconds.labels(
            method=request.method,
            route=route,
        ).observe(elapsed)

        return response


def _route_template(request: Request) -> str:
    """Return the matched route template, or the raw path if no match."""
    route = request.scope.get("route")
    if route is not None and hasattr(route, "path"):
        return str(route.path)
    return request.url.path


__all__ = [
    "AccessLogMiddleware",
    "CorrelationIdMiddleware",
    "GzipRequestMiddleware",
    "MetricsMiddleware",
]
