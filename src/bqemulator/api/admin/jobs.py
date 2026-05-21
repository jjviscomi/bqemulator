"""``GET /admin/jobs`` — diagnostic dump of every known job.

Reads the catalog's job table and returns a JSON array. The endpoint is
intentionally simple and project-agnostic: callers debugging a
misbehaving CI job want to see every job in flight, not just one
project's.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bqemulator.api.dependencies import AppContext, get_context

router = APIRouter(tags=["admin"])

_Ctx = Annotated[AppContext, Depends(get_context)]

# Real BigQuery caps list_jobs() at 1000 by default; mirror that so an
# accidentally-huge catalog can't lock up the diagnostic endpoint.
_DEFAULT_MAX_RESULTS = 1000


@router.get("/jobs")
def admin_list_jobs(
    ctx: _Ctx,
    project_id: Annotated[
        str | None,
        Query(alias="projectId", description="Filter to a single project (optional)."),
    ] = None,
    state: Annotated[
        str | None,
        Query(description="Filter to a job state (PENDING/RUNNING/DONE)."),
    ] = None,
    max_results: Annotated[
        int,
        Query(alias="maxResults", ge=1, le=10_000),
    ] = _DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """List every job the catalog tracks across (or within) projects."""
    if project_id is not None:
        jobs = ctx.catalog.list_jobs(
            project_id,
            state_filter=state,
            max_results=max_results,
        )
    else:
        # No protocol method enumerates projects, so we look at every
        # dataset's project_id and de-duplicate. Catalogs in practice have
        # at most a handful of projects.
        projects = sorted({d.project_id for d in ctx.catalog.list_all_datasets()})
        collected: list[Any] = []
        for pid in projects:
            collected.extend(
                ctx.catalog.list_jobs(
                    pid,
                    state_filter=state,
                    max_results=max_results,
                ),
            )
            if len(collected) >= max_results:
                break
        jobs = tuple(collected[:max_results])

    return {
        "kind": "bqemu#adminJobList",
        "totalItems": len(jobs),
        "jobs": [_job_to_admin_dict(j) for j in jobs],
    }


def _job_to_admin_dict(job: Any) -> dict[str, Any]:
    """Render a job as a plain dict for the admin JSON response."""
    return {
        "projectId": job.project_id,
        "jobId": job.job_id,
        "jobType": job.job_type,
        "state": job.state,
        "creationTime": job.creation_time.isoformat() if job.creation_time else None,
        "startTime": job.start_time.isoformat() if job.start_time else None,
        "endTime": job.end_time.isoformat() if job.end_time else None,
        "userEmail": job.user_email,
        "errorResult": job.error_result,
        "statistics": job.statistics,
    }
