"""Datasets REST routes.

Endpoints:
    GET    /bigquery/v2/projects/{p}/datasets           — list
    POST   /bigquery/v2/projects/{p}/datasets           — insert
    GET    /bigquery/v2/projects/{p}/datasets/{d}       — get
    PATCH  /bigquery/v2/projects/{p}/datasets/{d}       — patch
    PUT    /bigquery/v2/projects/{p}/datasets/{d}       — update
    DELETE /bigquery/v2/projects/{p}/datasets/{d}       — delete

The request/response shapes match the BigQuery REST API v2 exactly so
the official ``google-cloud-bigquery`` client can talk to us unmodified.

Reference:
    https://cloud.google.com/bigquery/docs/reference/rest/v2/datasets
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from bqemulator.api.dependencies import AppContext, get_context
from bqemulator.api.routes._rest_helpers import body_or_existing, existing_attr_or
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import AccessEntry, DatasetMeta
from bqemulator.domain.errors import (
    InvalidQueryError,
    ResourceRef,
    resource_not_found,
)
from bqemulator.storage.sql_identifiers import quoted_schema

router = APIRouter(prefix="/bigquery/v2", tags=["datasets"])

_Ctx = Annotated[AppContext, Depends(get_context)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _access_entry_to_rest(entry: AccessEntry) -> dict[str, Any]:
    """Serialize a single :class:`AccessEntry` to the BigQuery REST shape."""
    out: dict[str, Any] = {}
    if entry.role is not None:
        out["role"] = entry.role
    if entry.user_by_email is not None:
        out["userByEmail"] = entry.user_by_email
    if entry.group_by_email is not None:
        out["groupByEmail"] = entry.group_by_email
    if entry.domain is not None:
        out["domain"] = entry.domain
    if entry.special_group is not None:
        out["specialGroup"] = entry.special_group
    if entry.iam_member is not None:
        out["iamMember"] = entry.iam_member
    if entry.view is not None:
        proj, dataset, table = entry.view
        out["view"] = {
            "projectId": proj,
            "datasetId": dataset,
            "tableId": table,
        }
    if entry.routine is not None:
        proj, dataset, routine = entry.routine
        out["routine"] = {
            "projectId": proj,
            "datasetId": dataset,
            "routineId": routine,
        }
    if entry.dataset is not None:
        proj, dataset = entry.dataset
        out["dataset"] = {
            "projectId": proj,
            "datasetId": dataset,
        }
    return out


def _rest_to_access_entry(raw: dict[str, Any]) -> AccessEntry:
    """Build an :class:`AccessEntry` from a single REST entry."""
    view_ref = raw.get("view")
    routine_ref = raw.get("routine")
    dataset_ref = raw.get("dataset")
    return AccessEntry(
        role=raw.get("role"),
        user_by_email=raw.get("userByEmail"),
        group_by_email=raw.get("groupByEmail"),
        domain=raw.get("domain"),
        special_group=raw.get("specialGroup"),
        iam_member=raw.get("iamMember"),
        view=(
            (
                str(view_ref.get("projectId", "")),
                str(view_ref.get("datasetId", "")),
                str(view_ref.get("tableId", "")),
            )
            if isinstance(view_ref, dict)
            else None
        ),
        routine=(
            (
                str(routine_ref.get("projectId", "")),
                str(routine_ref.get("datasetId", "")),
                str(routine_ref.get("routineId", "")),
            )
            if isinstance(routine_ref, dict)
            else None
        ),
        dataset=(
            (
                str(dataset_ref.get("projectId", "")),
                str(dataset_ref.get("datasetId", "")),
            )
            if isinstance(dataset_ref, dict)
            else None
        ),
    )


def _parse_access_entries(raw: object) -> tuple[AccessEntry, ...]:
    """Parse a REST ``access`` array, validating shape."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise InvalidQueryError("access must be an array of access entries")
    return tuple(_rest_to_access_entry(item) for item in raw if isinstance(item, dict))


def _dataset_to_rest(ds: DatasetMeta) -> dict[str, Any]:
    """Serialize a :class:`DatasetMeta` to the BigQuery REST shape."""
    ref = {
        "projectId": ds.project_id,
        "datasetId": ds.dataset_id,
    }
    body: dict[str, Any] = {
        "kind": "bigquery#dataset",
        "id": f"{ds.project_id}:{ds.dataset_id}",
        "datasetReference": ref,
        "location": ds.location,
        "creationTime": str(int(ds.creation_time.timestamp() * 1000)),
        "lastModifiedTime": str(int(ds.last_modified_time.timestamp() * 1000)),
        "etag": ds.etag,
        # BigQuery's ``Dataset.type`` field is ``DEFAULT`` for ordinary
        # datasets and ``LINKED`` / ``EXTERNAL`` for the federated
        # variants the emulator doesn't model. Emit the constant so
        # clients that switch on the field find the documented value.
        # Required for ``datasets.list`` shape parity.
        "type": "DEFAULT",
        # BigQuery's documented default time-travel window for ordinary
        # datasets is 7 days = 168 hours. Clients use this to decide
        # whether AS-OF queries within the window can be served from
        # the time-travel index.
        "maxTimeTravelHours": "168",
    }
    if ds.friendly_name:
        body["friendlyName"] = ds.friendly_name
    if ds.description:
        body["description"] = ds.description
    if ds.labels:
        body["labels"] = ds.labels
    if ds.default_table_expiration_ms is not None:
        body["defaultTableExpirationMs"] = str(ds.default_table_expiration_ms)
    if ds.default_partition_expiration_ms is not None:
        body["defaultPartitionExpirationMs"] = str(ds.default_partition_expiration_ms)
    if ds.access_entries:
        body["access"] = [_access_entry_to_rest(a) for a in ds.access_entries]
    return body


def _rest_to_dataset_meta(
    project_id: str,
    body: dict[str, Any],
    clock: Any,
    existing: DatasetMeta | None = None,
) -> DatasetMeta:
    """Build a :class:`DatasetMeta` from a REST request body."""
    ref = body.get("datasetReference", {})
    dataset_id = ref.get("datasetId") or body.get("datasetId", "")
    now = clock.now()

    if "access" in body:
        access_entries = _parse_access_entries(body.get("access"))
    else:
        access_entries = existing.access_entries if existing else ()

    return DatasetMeta(
        project_id=project_id,
        dataset_id=dataset_id,
        friendly_name=body_or_existing(body, "friendlyName", existing, "friendly_name", None),
        description=body_or_existing(body, "description", existing, "description", None),
        labels=body_or_existing(body, "labels", existing, "labels", {}),
        location=body_or_existing(body, "location", existing, "location", "US"),
        default_table_expiration_ms=body_or_existing(
            body, "defaultTableExpirationMs", existing, "default_table_expiration_ms", None
        ),
        default_partition_expiration_ms=body_or_existing(
            body, "defaultPartitionExpirationMs", existing, "default_partition_expiration_ms", None
        ),
        access_entries=access_entries,
        creation_time=existing_attr_or(existing, "creation_time", now),
        last_modified_time=now,
        etag=generate_etag(project_id, dataset_id, str(now)),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/datasets")
def list_datasets(
    project_id: str,
    ctx: _Ctx,
    maxResults: int = Query(default=1000, alias="maxResults"),  # noqa: N803
) -> dict[str, Any]:
    """List datasets in a project."""
    datasets = ctx.catalog.list_datasets(project_id)
    items = [_dataset_to_rest(ds) for ds in datasets[:maxResults]]
    return {
        "kind": "bigquery#datasetList",
        "datasets": items,
        "totalItems": len(datasets),
    }


@router.post(
    "/projects/{project_id}/datasets",
    status_code=status.HTTP_200_OK,
)
async def insert_dataset(
    project_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Create a new dataset."""
    body = await request.json()
    meta = _rest_to_dataset_meta(project_id, body, ctx.clock)
    target_schema = quoted_schema(project_id, meta.dataset_id)
    async with ctx.engine.write_lock():
        # Create the DuckDB schema for the dataset.
        ctx.engine.execute(f"CREATE SCHEMA IF NOT EXISTS {target_schema}")
        created = ctx.catalog.create_dataset(meta)
    return _dataset_to_rest(created)


@router.get("/projects/{project_id}/datasets/{dataset_id}")
def get_dataset(
    project_id: str,
    dataset_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Get a dataset by ID."""
    ds = ctx.catalog.get_dataset(project_id, dataset_id)
    if ds is None:
        raise resource_not_found(ResourceRef("dataset", project_id, dataset_id))
    return _dataset_to_rest(ds)


@router.patch("/projects/{project_id}/datasets/{dataset_id}")
async def patch_dataset(
    project_id: str,
    dataset_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Partial update of a dataset."""
    existing = ctx.catalog.get_dataset(project_id, dataset_id)
    if existing is None:
        raise resource_not_found(ResourceRef("dataset", project_id, dataset_id))
    body = await request.json()
    updated = _rest_to_dataset_meta(
        project_id, {"datasetReference": {"datasetId": dataset_id}, **body}, ctx.clock, existing
    )
    result = ctx.catalog.update_dataset(updated)
    return _dataset_to_rest(result)


@router.put("/projects/{project_id}/datasets/{dataset_id}")
async def update_dataset(
    project_id: str,
    dataset_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Full replace of a dataset."""
    existing = ctx.catalog.get_dataset(project_id, dataset_id)
    if existing is None:
        raise resource_not_found(ResourceRef("dataset", project_id, dataset_id))
    body = await request.json()
    updated = _rest_to_dataset_meta(
        project_id, {"datasetReference": {"datasetId": dataset_id}, **body}, ctx.clock, existing
    )
    result = ctx.catalog.update_dataset(updated)
    return _dataset_to_rest(result)


@router.delete(
    "/projects/{project_id}/datasets/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_dataset(
    project_id: str,
    dataset_id: str,
    ctx: _Ctx,
    deleteContents: bool = Query(default=False, alias="deleteContents"),  # noqa: N803
) -> Response:
    """Delete a dataset."""
    async with ctx.engine.write_lock():
        if deleteContents:
            # Drop the DuckDB schema cascade.
            target_schema = quoted_schema(project_id, dataset_id)
            ctx.engine.execute(f"DROP SCHEMA IF EXISTS {target_schema} CASCADE")
        ctx.catalog.delete_dataset(
            project_id,
            dataset_id,
            delete_contents=deleteContents,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
