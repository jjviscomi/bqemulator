"""FastAPI dependency containers.

We avoid module-level singletons. Instead, the composition root constructs
an :class:`AppContext` and attaches it to the FastAPI app via ``app.state``.
Handlers and routes receive it via :func:`get_context`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import Request

from bqemulator.row_access.identity import (
    CallerIdentity,
    resolve_caller_from_headers,
)
from bqemulator.streaming.write_stream import WriteStreamManager

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.repository import CatalogRepository
    from bqemulator.config import Settings
    from bqemulator.domain.clock import Clock
    from bqemulator.domain.events import EventBus
    from bqemulator.jobs.upload_session_manager import UploadSessionManager
    from bqemulator.observability.metrics import MetricsRegistry
    from bqemulator.row_access.policy import RowAccessPolicyManager
    from bqemulator.storage.engine import DuckDBEngine
    from bqemulator.udf.runtime import UDFRegistry
    from bqemulator.versioning.snapshots import SnapshotManager


@dataclass(slots=True, frozen=True)
class AppContext:
    """Per-process dependency container attached to ``app.state``."""

    settings: Settings
    clock: Clock
    engine: DuckDBEngine
    catalog: CatalogRepository
    metrics: MetricsRegistry
    events: EventBus
    udf_registry: UDFRegistry
    snapshots: SnapshotManager
    row_access: RowAccessPolicyManager
    # Storage Write API in-memory stream registry, shared with the admin
    # /admin/streams endpoint. Defaults to a fresh instance so tests that
    # build an AppContext directly don't have to know about it.
    write_streams: WriteStreamManager = field(default_factory=WriteStreamManager)
    # Resumable upload session manager. Constructed lazily by the
    # composition root when the upload router is wired in. ``None`` in
    # contexts that don't need uploads (gRPC-only tests, unit fakes).
    upload_sessions: UploadSessionManager | None = None


def get_context(request: Request) -> AppContext:
    """FastAPI dependency that returns the :class:`AppContext`."""
    return request.app.state.context  # type: ignore[no-any-return]


def get_caller(request: Request) -> CallerIdentity:
    """FastAPI dependency that returns the caller identity for this request.

    See ADR 0018 for the resolution rules.
    """
    return resolve_caller_from_headers(request.headers)


__all__ = ["AppContext", "get_caller", "get_context"]
