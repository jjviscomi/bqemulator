"""Diagnostic ``/admin/*`` HTTP endpoints.

These endpoints are **opt-in**: they are wired into the FastAPI app only
when ``Settings.admin_enabled`` is True (CLI flag ``--enable-admin`` or
env var ``BQEMU_ADMIN_ENABLED=1``).

Routes:

* :mod:`bqemulator.api.admin.jobs`     — ``GET /admin/jobs``
* :mod:`bqemulator.api.admin.catalog`  — ``GET /admin/catalog``
* :mod:`bqemulator.api.admin.streams`  — ``GET /admin/streams``
* :mod:`bqemulator.api.admin.config`   — ``GET /admin/config``

The endpoints are **read-only** and serve JSON. There is no
authentication — the opt-in flag is the only gate (mirrors the rest of
the emulator's "trust the local environment" stance; ADR 0020 records
the threat model). Do not enable in untrusted environments.
"""

from __future__ import annotations

from fastapi import APIRouter

from bqemulator.api.admin.catalog import router as catalog_router
from bqemulator.api.admin.config import router as config_router
from bqemulator.api.admin.jobs import router as jobs_router
from bqemulator.api.admin.streams import router as streams_router


def build_admin_router() -> APIRouter:
    """Combine every ``/admin/*`` sub-router into one router for inclusion."""
    parent = APIRouter(prefix="/admin", tags=["admin"])
    parent.include_router(jobs_router)
    parent.include_router(catalog_router)
    parent.include_router(streams_router)
    parent.include_router(config_router)
    return parent


__all__ = ["build_admin_router"]
