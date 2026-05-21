"""``GET /admin/catalog`` — diagnostic dump of the dataset / table / routine catalog.

The endpoint walks every dataset and (per dataset) every table and
routine. Output shape is JSON, project-grouped, and ordered for
predictable diffs in test fixtures.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bqemulator.api.dependencies import AppContext, get_context

router = APIRouter(tags=["admin"])

_Ctx = Annotated[AppContext, Depends(get_context)]


@router.get("/catalog")
def admin_dump_catalog(
    ctx: _Ctx,
    project_id: Annotated[
        str | None,
        Query(alias="projectId", description="Filter to a single project."),
    ] = None,
) -> dict[str, Any]:
    """Return the full dataset / table / routine catalog as JSON."""
    datasets = ctx.catalog.list_all_datasets()
    if project_id is not None:
        datasets = tuple(d for d in datasets if d.project_id == project_id)
    datasets = tuple(sorted(datasets, key=lambda d: (d.project_id, d.dataset_id)))

    projects: dict[str, dict[str, Any]] = {}
    for ds in datasets:
        proj = projects.setdefault(
            ds.project_id,
            {"projectId": ds.project_id, "datasets": []},
        )
        tables = tuple(
            sorted(
                ctx.catalog.list_tables(ds.project_id, ds.dataset_id),
                key=lambda t: t.table_id,
            ),
        )
        routines = tuple(
            sorted(
                ctx.catalog.list_routines(ds.project_id, ds.dataset_id),
                key=lambda r: r.routine_id,
            ),
        )
        proj["datasets"].append(
            {
                "datasetId": ds.dataset_id,
                "location": ds.location,
                "labels": dict(ds.labels),
                "tables": [_table_summary(t) for t in tables],
                "routines": [_routine_summary(r) for r in routines],
            },
        )

    return {
        "kind": "bqemu#adminCatalog",
        "totalProjects": len(projects),
        "totalDatasets": len(datasets),
        "projects": list(projects.values()),
    }


def _table_summary(table: Any) -> dict[str, Any]:
    """Render a TableMeta into the admin-friendly summary shape."""
    return {
        "tableId": table.table_id,
        "tableType": table.table_type,
        "numRows": table.num_rows,
        "numBytes": table.num_bytes,
        "schemaFields": tuple(f.name for f in table.schema_.fields),
        "partitioned": table.time_partitioning is not None or table.range_partitioning is not None,
        "clustered": table.clustering is not None,
    }


def _routine_summary(routine: Any) -> dict[str, Any]:
    """Render a RoutineMeta into the admin-friendly summary shape."""
    return {
        "routineId": routine.routine_id,
        "routineType": routine.routine_type,
        "language": routine.language,
    }
