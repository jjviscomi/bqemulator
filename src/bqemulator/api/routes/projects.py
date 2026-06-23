"""Projects REST routes.

Endpoints:
    GET /bigquery/v2/projects                             — list
    GET /bigquery/v2/projects/{p}/serviceAccount          — getServiceAccount

Reference:
    https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/projects
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from bqemulator.api.dependencies import AppContext, get_context

router = APIRouter(prefix="/bigquery/v2", tags=["projects"])

_Ctx = Annotated[AppContext, Depends(get_context)]


@router.get("/projects")
def list_projects(ctx: _Ctx) -> dict[str, Any]:
    """List projects known to the emulator.

    The emulator always reports the default project; additional
    projects are created implicitly when datasets are inserted.
    """
    project_id = ctx.settings.default_project_id
    return {
        "kind": "bigquery#projectList",
        "projects": [
            {
                "kind": "bigquery#project",
                "id": project_id,
                "projectReference": {"projectId": project_id},
                "friendlyName": project_id,
            },
        ],
        "totalItems": 1,
    }


@router.get("/projects/{project_id}/serviceAccount")
def get_service_account(project_id: str, ctx: _Ctx) -> dict[str, Any]:  # noqa: ARG001
    """Return a deterministic fake service-account email.

    The real API returns the project's BigQuery service account. We
    return a predictable email so client code that reads it (e.g. to
    grant permissions) works without error.
    """
    return {
        "kind": "bigquery#getServiceAccountResponse",
        "email": f"bqemulator@{project_id}.iam.gserviceaccount.com",
    }
