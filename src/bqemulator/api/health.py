"""Health endpoints: ``/healthz`` (liveness) and ``/readyz`` (readiness)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from bqemulator import __version__
from bqemulator.api.dependencies import AppContext, get_context

router = APIRouter(tags=["health"])

_Context = Annotated[AppContext, Depends(get_context)]


@router.get("/healthz", summary="Liveness probe", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Return ``{"status": "ok"}`` as long as the process is alive."""
    return {"status": "ok", "version": __version__}


@router.get("/readyz", summary="Readiness probe", include_in_schema=False)
def readyz(
    response: Response,
    ctx: _Context,
) -> dict[str, object]:
    """Return a readiness report.

    The emulator is ready when:

    * DuckDB engine has been started and answers a trivial query.
    * Catalog repository responds to a list call.
    """
    checks: dict[str, object] = {}
    overall_ok = True

    # DuckDB liveness.
    try:
        ctx.engine.execute("SELECT 1").fetchone()
        checks["duckdb"] = "ok"
    except Exception as exc:  # noqa: BLE001
        overall_ok = False
        checks["duckdb"] = f"error: {exc}"

    # Catalog liveness.
    try:
        ctx.catalog.list_datasets(ctx.settings.default_project_id)
        checks["catalog"] = "ok"
    except Exception as exc:  # noqa: BLE001
        overall_ok = False
        checks["catalog"] = f"error: {exc}"

    if not overall_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if overall_ok else "degraded",
        "version": __version__,
        "checks": checks,
    }
