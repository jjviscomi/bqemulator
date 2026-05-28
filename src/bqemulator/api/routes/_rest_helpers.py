"""Shared helpers for REST request-body ↔ catalog-meta mapping.

The dataset / routine upsert handlers resolve each field from three
sources, in priority order: the request body, the existing resource
(for PATCH / idempotent PUT), then a static default. Centralising that
three-way coalesce keeps the per-field branching out of the mapper
functions — both for readability and to hold them under the
cyclomatic-complexity ceiling.
"""

from __future__ import annotations

from typing import Any


def existing_attr_or(existing: Any, attr: str, default: Any) -> Any:
    """Return ``existing.attr`` when *existing* is set, else *default*."""
    return getattr(existing, attr) if existing is not None else default


def body_or_existing(
    body: dict[str, Any],
    key: str,
    existing: Any,
    attr: str,
    default: Any,
) -> Any:
    """Three-way field resolve: request *body*, then *existing*, then *default*.

    Mirrors ``body.get(key, existing.attr if existing else default)`` while
    keeping the ``key in body`` semantics exact — a body value of ``None``
    wins over the existing / default fallback, just as ``dict.get`` does.
    """
    if key in body:
        return body[key]
    return existing_attr_or(existing, attr, default)
