"""FastAPI exception handlers that render BigQuery ErrorProto responses.

Every :class:`DomainError` is translated to the exact JSON shape the real
BigQuery service returns. Uncaught exceptions are logged and rendered as
``internalError``.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from bqemulator.domain.errors import (
    DomainError,
    ErrorDetail,
    InternalError,
    ValidationError,
)
from bqemulator.observability.logging_ import get_logger

_log = get_logger(__name__)


def install_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on ``app``."""

    @app.exception_handler(json.JSONDecodeError)
    async def _json_decode_handler(
        _request: Request,
        exc: json.JSONDecodeError,
    ) -> JSONResponse:
        # A malformed (or empty) request body raises JSONDecodeError when a
        # route does ``await request.json()``. Without this handler the
        # default exception handler returns a generic 500 "internal error",
        # which is what real BigQuery clients see as an outage. Route the
        # condition to a proper 400 with the BigQuery ErrorProto shape so
        # the client receives the same diagnostic real BigQuery produces.
        bq_error = ValidationError(
            f"Request body is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
            details=[
                ErrorDetail(
                    reason="invalid",
                    message="Malformed JSON in request body",
                    location="body",
                ),
            ],
        )
        _log.info(
            "domain.error",
            type="JSONDecodeError",
            message=bq_error.message,
            reason=bq_error.bq_reason,
        )
        return JSONResponse(
            status_code=bq_error.http_status,
            content=bq_error.to_bigquery_error(),
        )

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        _log.info(
            "domain.error",
            type=type(exc).__name__,
            message=exc.message,
            reason=exc.bq_reason,
        )
        return JSONResponse(status_code=exc.http_status, content=exc.to_bigquery_error())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Flatten pydantic errors into BigQuery's ErrorProto.errors[] structure.
        details: list[ErrorDetail] = []
        for err in exc.errors():
            location = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            details.append(
                ErrorDetail(
                    reason="invalid",
                    message=f"{err.get('msg', 'Invalid value')} at {location}",
                    location=location or None,
                ),
            )
        bq_error = ValidationError(
            "Request validation failed",
            details=details,
        )
        return JSONResponse(
            status_code=bq_error.http_status,
            content=bq_error.to_bigquery_error(),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        _log.exception("unhandled.exception", exc_info=exc)
        wrapped = InternalError("An internal error occurred")
        return JSONResponse(
            status_code=wrapped.http_status,
            content=wrapped.to_bigquery_error(),
        )


__all__ = ["install_exception_handlers"]
