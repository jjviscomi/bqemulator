"""Row access policies REST routes.

Endpoints (mirroring the BigQuery REST v2 ``rowAccessPolicies``
resource as documented in the discovery doc):

    GET    /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies                 — list
    POST   /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies                 — insert
    GET    /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies/{policyId}      — get
    PUT    /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies/{policyId}      — update
    DELETE /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies/{policyId}      — delete
    POST   /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies:batchDelete     — batchDelete
    POST   /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies/{policyId}:getIamPolicy
    POST   /…/{p}/datasets/{d}/tables/{t}/rowAccessPolicies/{policyId}:testIamPermissions

Reference:
    https://cloud.google.com/bigquery/docs/reference/rest/v2/rowAccessPolicies
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status

from bqemulator.api.dependencies import AppContext, get_context
from bqemulator.domain.errors import (
    InvalidQueryError,
    ResourceRef,
    resource_not_found,
)

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.models import RowAccessPolicyMeta

router = APIRouter(prefix="/bigquery/v2", tags=["row_access_policies"])

_Ctx = Annotated[AppContext, Depends(get_context)]

# IAM role assigned to grantees of a row access policy. The emulator
# does not enforce IAM, but we surface the role name so client code
# that round-trips ``getIamPolicy`` results sees the same shape it
# would receive from real BigQuery.
_RAP_VIEWER_ROLE = "roles/bigquery.filteredDataViewer"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _policy_to_rest(policy: RowAccessPolicyMeta) -> dict[str, Any]:
    """Serialize a RAP to the BigQuery REST shape.

    ``grantees`` is documented as Input-Only on the BigQuery API (the
    field is accepted on insert/update; the canonical read path is
    ``getIamPolicy``). For the emulator we still echo grantees back
    on read responses so test code can assert on the policy contents
    without making a second IAM call. Real BigQuery clients tolerate
    additional fields gracefully.
    """
    return {
        "rowAccessPolicyReference": {
            "projectId": policy.project_id,
            "datasetId": policy.dataset_id,
            "tableId": policy.table_id,
            "policyId": policy.policy_id,
        },
        "filterPredicate": policy.filter_predicate,
        "grantees": list(policy.grantees),
        "creationTime": str(int(policy.creation_time.timestamp() * 1000)),
        "lastModifiedTime": str(int(policy.last_modified_time.timestamp() * 1000)),
        "etag": policy.etag,
    }


def _parse_reference(
    body: dict[str, Any],
    *,
    project_id: str,
    dataset_id: str,
    table_id: str,
) -> str:
    """Extract policy_id from a request body, supporting both shapes."""
    ref = body.get("rowAccessPolicyReference") or {}
    policy_id = ref.get("policyId")
    if not policy_id:
        raise InvalidQueryError(
            "rowAccessPolicyReference.policyId is required",
        )
    # The reference object's ids must agree with the URL — BigQuery
    # treats a mismatch as a 400. Mirror that to avoid silent confusion.
    for url_value, ref_key, label in (
        (project_id, "projectId", "project"),
        (dataset_id, "datasetId", "dataset"),
        (table_id, "tableId", "table"),
    ):
        ref_value = ref.get(ref_key)
        if ref_value is not None and ref_value != url_value:
            raise InvalidQueryError(
                f"rowAccessPolicyReference.{ref_key}={ref_value!r} does not "
                f"match URL {label}={url_value!r}",
            )
    return str(policy_id)


def _normalise_grantees(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise InvalidQueryError("grantees must be an array of strings")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise InvalidQueryError("grantees must be an array of strings")
        out.append(item)
    return tuple(out)


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies",
)
def list_row_access_policies(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """List row access policies on the table."""
    policies = ctx.row_access.list_for_table(project_id, dataset_id, table_id)
    return {
        "rowAccessPolicies": [_policy_to_rest(p) for p in policies],
    }


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies",
    status_code=status.HTTP_200_OK,
)
async def insert_row_access_policy(
    project_id: str,
    dataset_id: str,
    table_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Create a new row access policy."""
    body = await request.json()
    policy_id = _parse_reference(
        body,
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
    )
    filter_predicate = body.get("filterPredicate")
    if not filter_predicate:
        raise InvalidQueryError("filterPredicate is required")
    grantees = _normalise_grantees(body.get("grantees"))
    created = ctx.row_access.create(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        policy_id=policy_id,
        filter_predicate=str(filter_predicate),
        grantees=grantees,
    )
    return _policy_to_rest(created)


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies/{policy_id}",
)
def get_row_access_policy(
    project_id: str,
    dataset_id: str,
    table_id: str,
    policy_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Get a single row access policy."""
    policy = ctx.row_access.get(project_id, dataset_id, table_id, policy_id)
    if policy is None:
        raise resource_not_found(
            ResourceRef(
                "row_access_policy",
                project_id,
                dataset_id,
                table_id,
            ),
        )
    return _policy_to_rest(policy)


@router.put(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies/{policy_id}",
)
async def update_row_access_policy(
    project_id: str,
    dataset_id: str,
    table_id: str,
    policy_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Replace an existing row access policy.

    Per the BigQuery discovery doc the update verb is PUT, not PATCH.
    The body shape matches the insert body.
    """
    body = await request.json()
    # If the body carries a rowAccessPolicyReference it must agree with
    # the URL; the URL is authoritative regardless.
    if "rowAccessPolicyReference" in body:
        body_policy_id = _parse_reference(
            body,
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
        )
        if body_policy_id != policy_id:
            raise InvalidQueryError(
                f"rowAccessPolicyReference.policyId={body_policy_id!r} does "
                f"not match URL policyId={policy_id!r}",
            )
    filter_predicate = body.get("filterPredicate")
    if not filter_predicate:
        raise InvalidQueryError("filterPredicate is required")
    grantees = _normalise_grantees(body.get("grantees"))
    updated = ctx.row_access.update(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        policy_id=policy_id,
        filter_predicate=str(filter_predicate),
        grantees=grantees,
    )
    return _policy_to_rest(updated)


@router.delete(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_row_access_policy(
    project_id: str,
    dataset_id: str,
    table_id: str,
    policy_id: str,
    ctx: _Ctx,
) -> Response:
    """Delete a row access policy."""
    ctx.row_access.delete(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        policy_id=policy_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies:batchDelete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def batch_delete_row_access_policies(
    project_id: str,
    dataset_id: str,
    table_id: str,
    request: Request,
    ctx: _Ctx,
) -> Response:
    """Delete a list of row access policies atomically."""
    body = await request.json()
    raw_ids = body.get("policyIds")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise InvalidQueryError("policyIds must be a non-empty array of strings")
    policy_ids: tuple[str, ...] = tuple(str(p) for p in raw_ids)
    force = bool(body.get("force", False))
    ctx.row_access.batch_delete(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        policy_ids=policy_ids,
        force=force,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# IAM-shaped endpoints (parity with BigQuery REST v2)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
    "/rowAccessPolicies/{policy_id}:getIamPolicy",
)
async def get_iam_policy(
    project_id: str,
    dataset_id: str,
    table_id: str,
    policy_id: str,
    request: Request,  # noqa: ARG001 — body fields ignored, but FastAPI logs the verb
    ctx: _Ctx,
) -> dict[str, Any]:
    """Return the IAM policy that backs the row access policy.

    BigQuery models the grantees of a row access policy as IAM
    bindings on the policy resource, so ``getIamPolicy`` is the
    documented read path for them. We return the same shape with a
    single binding, ``roles/bigquery.filteredDataViewer``, listing
    the policy's grantees as members.
    """
    policy = ctx.row_access.get(project_id, dataset_id, table_id, policy_id)
    if policy is None:
        raise resource_not_found(
            ResourceRef(
                "row_access_policy",
                project_id,
                dataset_id,
                table_id,
            ),
        )
    bindings: list[dict[str, Any]] = []
    if policy.grantees:
        bindings.append(
            {
                "role": _RAP_VIEWER_ROLE,
                "members": list(policy.grantees),
            },
        )
    # IAM Policy.etag is a base64-encoded byte string. Reuse the
    # policy's etag with a deterministic encoding so consumers can
    # round-trip it safely.
    etag_bytes = policy.etag.encode("ascii")
    return {
        "version": 1,
        "bindings": bindings,
        "etag": base64.b64encode(etag_bytes).decode("ascii"),
    }


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
    "/rowAccessPolicies/{policy_id}:testIamPermissions",
)
async def test_iam_permissions(
    project_id: str,
    dataset_id: str,
    table_id: str,
    policy_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Stub for parity with the BigQuery REST surface.

    The emulator does not enforce IAM, so any permission a client
    asks about is reported as held. Documented in
    ``docs/reference/out-of-scope.md#iam-enforcement``.
    """
    # Surface a NotFoundError on missing policies even though we don't
    # consult IAM — clients rely on this to detect typos.
    if ctx.row_access.get(project_id, dataset_id, table_id, policy_id) is None:
        raise resource_not_found(
            ResourceRef(
                "row_access_policy",
                project_id,
                dataset_id,
                table_id,
            ),
        )
    body = await request.json()
    requested = body.get("permissions")
    if not isinstance(requested, list):
        return {"permissions": []}
    return {"permissions": [str(p) for p in requested]}


__all__ = ["router"]
