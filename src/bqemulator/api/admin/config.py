"""``GET /admin/config`` — diagnostic dump of effective Settings.

Pydantic's :meth:`Settings.model_dump` already handles primitive types,
``StrEnum`` values, and ``Path`` objects (the latter via ``str()`` after
field validation). We coerce the dump to ``mode="json"`` so the response
is directly JSON-serialisable without further conversion.

There are no secrets in :class:`~bqemulator.config.Settings` at the time
of writing, but the redaction allow-list lives here so future additions
(e.g. credentials for the ``import`` extra) can be added safely.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from bqemulator.api.dependencies import AppContext, get_context

router = APIRouter(tags=["admin"])

_Ctx = Annotated[AppContext, Depends(get_context)]

# Field names that should be replaced with the literal "[REDACTED]" in
# the response. Empty today; new fields that wrap credentials, tokens,
# or other secrets MUST be added here before they ship.
_REDACTED_FIELDS: frozenset[str] = frozenset()


@router.get("/config")
def admin_dump_config(ctx: _Ctx) -> dict[str, Any]:
    """Return the active :class:`Settings` as JSON, with secrets redacted."""
    raw = ctx.settings.model_dump(mode="json")
    redacted: dict[str, Any] = {
        k: ("[REDACTED]" if k in _REDACTED_FIELDS and v is not None else v) for k, v in raw.items()
    }
    return {
        "kind": "bqemu#adminConfig",
        "settings": redacted,
    }
