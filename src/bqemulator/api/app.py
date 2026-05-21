"""FastAPI application factory.

The composition root (:mod:`bqemulator.server`) calls :func:`create_app`
with an :class:`AppContext`. The factory wires middleware, exception
handlers, and routers, then attaches the context to ``app.state`` so
handlers can resolve it via :func:`bqemulator.api.dependencies.get_context`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI

from bqemulator import __version__
from bqemulator.api.errors import install_exception_handlers
from bqemulator.api.health import router as health_router
from bqemulator.api.middleware import (
    AccessLogMiddleware,
    CorrelationIdMiddleware,
    GzipRequestMiddleware,
    MetricsMiddleware,
)
from bqemulator.api.routes.datasets import router as datasets_router
from bqemulator.api.routes.jobs import router as jobs_router
from bqemulator.api.routes.projects import router as projects_router
from bqemulator.api.routes.routines import router as routines_router
from bqemulator.api.routes.row_access_policies import (
    router as row_access_policies_router,
)
from bqemulator.api.routes.tabledata import router as tabledata_router
from bqemulator.api.routes.tables import router as tables_router
from bqemulator.api.routes.upload import router as upload_router
from bqemulator.observability.metrics import metrics_router

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext


def create_app(context: AppContext) -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="bqemulator",
        description="Local emulator for Google BigQuery",
        version=__version__,
        docs_url="/docs" if context.settings.admin_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if context.settings.admin_enabled else None,
    )
    app.state.context = context

    # Middleware — order matters (outer wraps inner). add_middleware
    # installs in the order called but wraps inside-out, so the last
    # call here is the outermost. GzipRequestMiddleware must run before
    # any downstream middleware/handler observes the request body.
    app.add_middleware(MetricsMiddleware, metrics=context.metrics)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(GzipRequestMiddleware)

    # Exception handlers.
    install_exception_handlers(app)

    # Routers.
    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(datasets_router)
    app.include_router(tables_router)
    app.include_router(tabledata_router)
    app.include_router(routines_router)
    app.include_router(row_access_policies_router)
    app.include_router(jobs_router)
    # G2 — upload host (multipart + resumable). Mounted at
    # /upload/bigquery/v2 to match BigQuery's documented upload URL prefix.
    app.include_router(upload_router)
    # Admin diagnostic endpoints — opt-in via Settings.admin_enabled.
    # The flag is OFF by default; users enable it for local debugging
    # only. See ADR 0020 for the threat model.
    if context.settings.admin_enabled:
        from bqemulator.api.admin import build_admin_router

        app.include_router(build_admin_router())
    if context.settings.metrics_enabled:
        app.include_router(metrics_router(context.metrics))

    # Record build info metric at startup.
    context.metrics.build_info.labels(version=__version__).set(1)

    return app


__all__ = ["create_app"]
