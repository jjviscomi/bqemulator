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
    """Resolve an attribute from an existing catalog object, with a fallback.

    Args:
        existing: The catalog-meta object the upsert handler is updating
            (e.g. :class:`bqemulator.catalog.models.DatasetMeta` or
            :class:`bqemulator.catalog.models.RoutineMeta`), or ``None`` on
            a fresh insert.
        attr: Attribute name to read from ``existing``.
        default: Value returned when ``existing`` is ``None``.

    Returns:
        ``getattr(existing, attr)`` when ``existing`` is not ``None``,
        otherwise ``default``. The function is side-effect-free.
    """
    return getattr(existing, attr) if existing is not None else default


def body_or_existing(
    body: dict[str, Any],
    key: str,
    existing: Any,
    attr: str,
    default: Any,
) -> Any:
    """Resolve a REST field with request-body precedence (three-way coalesce).

    Mirrors ``body.get(key, existing.attr if existing else default)`` while
    keeping the ``key in body`` semantics exact — a body value of ``None``
    wins over the existing / default fallback, just as ``dict.get`` does.

    Args:
        body: The decoded REST request payload.
        key: REST field name to look up in ``body``.
        existing: The catalog-meta object the upsert handler is updating
            (e.g. :class:`bqemulator.catalog.models.DatasetMeta` or
            :class:`bqemulator.catalog.models.RoutineMeta`), or ``None`` on
            a fresh insert.
        attr: Attribute name on ``existing`` to read when ``key`` is absent
            from ``body``.
        default: Value returned when ``key`` is absent and ``existing`` is
            ``None``.

    Returns:
        ``body[key]`` when ``key in body`` (even if the value is ``None``);
        otherwise :func:`existing_attr_or` applied to ``existing``, ``attr``,
        and ``default``. The function is side-effect-free.
    """
    if key in body:
        return body[key]
    return existing_attr_or(existing, attr, default)
