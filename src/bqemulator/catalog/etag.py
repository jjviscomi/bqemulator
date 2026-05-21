"""ETag generation for optimistic concurrency control.

BigQuery returns an ``etag`` on every resource. Clients may send
``If-Match: <etag>`` headers on update/delete requests. The emulator
honours this for tables and datasets, rejecting stale writes with
HTTP 412 Precondition Failed.

The ETag is a deterministic hash of the resource's identity + last
modification time. It does NOT need to be cryptographically strong —
it only needs to change when the resource changes.
"""

from __future__ import annotations

from datetime import datetime
import hashlib


def generate_etag(*parts: str | int | datetime) -> str:
    """Produce a short, stable ETag from the given parts.

    Args:
        *parts: Values that together identify the resource version.
                Typically ``(project_id, dataset_id, table_id,
                last_modified_time_iso)``.

    Returns:
        A quoted ETag string (``'"abc123…"'``), matching the HTTP
        ``ETag`` header format that the real BigQuery service returns.
    """
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(str(part).encode())
    # First 16 hex chars — short enough for headers, long enough to
    # avoid accidental collisions in any realistic emulator workload.
    digest = hasher.hexdigest()[:16]
    return f'"{digest}"'


__all__ = ["generate_etag"]
