"""Models (BigQuery ML) REST routes.

Endpoints:
    GET    /bigquery/v2/projects/{p}/datasets/{d}/models           — list
    GET    /bigquery/v2/projects/{p}/datasets/{d}/models/{m}       — get
    PATCH  /bigquery/v2/projects/{p}/datasets/{d}/models/{m}       — patch
    DELETE /bigquery/v2/projects/{p}/datasets/{d}/models/{m}       — delete

The BigQuery Models REST resource has **no** ``insert`` method: models
are created only by ``CREATE MODEL`` jobs, not by this resource.
Only the mutable fields documented by the official client
(``description``, ``friendlyName``, ``labels``, ``expirationTime``,
``encryptionConfiguration``) are accepted on ``PATCH``; ``modelType``,
``featureColumns``, ``labelColumns``, ``creationTime``, and ``location``
are read-only and carried through unchanged. See ADR 0047 / RFC 0002 for
the surface-only BigQuery ML scope.

Reference:
    https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/models
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import ValidationError as PydanticValidationError

from bqemulator.api.dependencies import AppContext, get_context
from bqemulator.api.routes._rest_helpers import body_or_existing
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import ModelMeta
from bqemulator.domain.errors import (
    ResourceRef,
    ValidationError,
    resource_not_found,
)

router = APIRouter(prefix="/bigquery/v2", tags=["models"])

_Ctx = Annotated[AppContext, Depends(get_context)]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _to_millis(value: datetime) -> str:
    """Render a datetime as BigQuery's millisecond-epoch string."""
    return str(int(value.timestamp() * 1000))


def _from_millis(value: str | int) -> datetime:
    """Parse a BigQuery millisecond-epoch value into a UTC datetime."""
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _model_to_rest(m: ModelMeta) -> dict[str, Any]:
    """Serialize a :class:`ModelMeta` to the BigQuery REST shape.

    Emits only documented BigQuery fields; the internal ``training_query``
    provenance and a top-level ``kind`` are intentionally omitted (the
    conformance corpus is the source of truth for the exact envelope).
    """
    body: dict[str, Any] = {
        "etag": m.etag,
        "modelReference": {
            "projectId": m.project_id,
            "datasetId": m.dataset_id,
            "modelId": m.model_id,
        },
        "creationTime": _to_millis(m.creation_time),
        "lastModifiedTime": _to_millis(m.last_modified_time),
        "modelType": m.model_type,
        "location": m.location,
    }
    if m.friendly_name:
        body["friendlyName"] = m.friendly_name
    if m.description:
        body["description"] = m.description
    if m.labels:
        body["labels"] = dict(m.labels)
    if m.expiration_time is not None:
        body["expirationTime"] = _to_millis(m.expiration_time)
    if m.feature_columns:
        body["featureColumns"] = [dict(c) for c in m.feature_columns]
    if m.label_columns:
        body["labelColumns"] = [dict(c) for c in m.label_columns]
    if m.encryption_configuration is not None:
        body["encryptionConfiguration"] = dict(m.encryption_configuration)
    return body


def _patched_expiration(body: dict[str, Any], existing: ModelMeta) -> datetime | None:
    """Resolve ``expirationTime`` for a PATCH, honouring explicit null.

    A body value of ``null`` clears the expiry; an absent key leaves the
    existing value untouched; a millis value sets it. A non-numeric or
    out-of-range value is rejected with a 400 rather than surfacing as a
    500.
    """
    if "expirationTime" not in body:
        return existing.expiration_time
    raw = body["expirationTime"]
    if raw is None:
        return None
    try:
        return _from_millis(raw)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise ValidationError(f"Invalid expirationTime: {raw!r}") from exc


def _rest_to_model_meta(
    body: dict[str, Any],
    clock: Any,
    existing: ModelMeta,
) -> ModelMeta:
    """Apply a PATCH body to an existing model, returning the updated meta.

    Only the mutable fields are read from ``body``; every read-only field
    (identity, ``model_type``, feature/label columns, ``location``,
    ``creation_time``, training-query provenance) is preserved from
    ``existing``. The result is built through the validating constructor
    (not ``model_copy``, which skips validation) so an ill-typed mutable
    field — e.g. ``labels: null`` — is rejected with a 400 instead of
    being persisted as a row that would later fail catalog hydration.
    """
    now = clock.now()
    try:
        return ModelMeta(
            project_id=existing.project_id,
            dataset_id=existing.dataset_id,
            model_id=existing.model_id,
            model_type=existing.model_type,
            friendly_name=body_or_existing(body, "friendlyName", existing, "friendly_name", None),
            description=body_or_existing(body, "description", existing, "description", None),
            labels=body_or_existing(body, "labels", existing, "labels", {}),
            location=existing.location,
            expiration_time=_patched_expiration(body, existing),
            feature_columns=existing.feature_columns,
            label_columns=existing.label_columns,
            encryption_configuration=body_or_existing(
                body, "encryptionConfiguration", existing, "encryption_configuration", None
            ),
            training_query=existing.training_query,
            creation_time=existing.creation_time,
            last_modified_time=now,
            etag=generate_etag(
                existing.project_id, existing.dataset_id, existing.model_id, str(now)
            ),
        )
    except PydanticValidationError as exc:
        raise ValidationError("Invalid model patch body") from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/datasets/{dataset_id}/models")
def list_models(
    project_id: str,
    dataset_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """List the models in a dataset, ordered by ``model_id``.

    Returns a single ``models`` array, matching BigQuery's single-page
    ``ListModelsResponse`` for the sizes a local emulator serves; the
    codebase's other list endpoints do not paginate either.
    """
    models = sorted(
        ctx.catalog.list_models(project_id, dataset_id),
        key=lambda m: m.model_id,
    )
    return {"models": [_model_to_rest(m) for m in models]}


@router.get("/projects/{project_id}/datasets/{dataset_id}/models/{model_id}")
def get_model(
    project_id: str,
    dataset_id: str,
    model_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Get a model by ID."""
    m = ctx.catalog.get_model(project_id, dataset_id, model_id)
    if m is None:
        raise resource_not_found(ResourceRef("model", project_id, dataset_id, model_id))
    return _model_to_rest(m)


@router.patch("/projects/{project_id}/datasets/{dataset_id}/models/{model_id}")
async def patch_model(
    project_id: str,
    dataset_id: str,
    model_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Partial update of a model's mutable metadata."""
    body = await request.json()
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object")
    # Read-modify-write under the lock so concurrent PATCHes can't lose updates.
    async with ctx.engine.write_lock():
        existing = ctx.catalog.get_model(project_id, dataset_id, model_id)
        if existing is None:
            raise resource_not_found(ResourceRef("model", project_id, dataset_id, model_id))
        updated = _rest_to_model_meta(body, ctx.clock, existing)
        result = ctx.catalog.update_model(updated)
    return _model_to_rest(result)


@router.delete(
    "/projects/{project_id}/datasets/{dataset_id}/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_model(
    project_id: str,
    dataset_id: str,
    model_id: str,
    ctx: _Ctx,
) -> Response:
    """Delete a model."""
    async with ctx.engine.write_lock():
        if ctx.catalog.get_model(project_id, dataset_id, model_id) is None:
            raise resource_not_found(ResourceRef("model", project_id, dataset_id, model_id))
        ctx.catalog.delete_model(project_id, dataset_id, model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
