"""Routines REST routes.

Endpoints:
    GET    /bigquery/v2/projects/{p}/datasets/{d}/routines       — list
    POST   /bigquery/v2/projects/{p}/datasets/{d}/routines       — insert
    GET    /bigquery/v2/projects/{p}/datasets/{d}/routines/{r}   — get
    PATCH  /bigquery/v2/projects/{p}/datasets/{d}/routines/{r}   — patch
    PUT    /bigquery/v2/projects/{p}/datasets/{d}/routines/{r}   — update
    DELETE /bigquery/v2/projects/{p}/datasets/{d}/routines/{r}   — delete

Reference:
    https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/routines
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status

from bqemulator.api.dependencies import AppContext, get_context
from bqemulator.api.routes._rest_helpers import body_or_existing, existing_attr_or
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import RoutineArgument, RoutineMeta
from bqemulator.domain.errors import (
    InvalidQueryError,
    ResourceRef,
    resource_already_exists,
    resource_not_found,
)

router = APIRouter(prefix="/bigquery/v2", tags=["routines"])

_Ctx = Annotated[AppContext, Depends(get_context)]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _arg_to_rest(arg: RoutineArgument) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": arg.name,
        "argumentKind": arg.argument_kind,
        "mode": arg.mode,
    }
    if arg.data_type is not None:
        out["dataType"] = arg.data_type
    return out


def _routine_to_rest(r: RoutineMeta) -> dict[str, Any]:
    body: dict[str, Any] = {
        "routineReference": {
            "projectId": r.project_id,
            "datasetId": r.dataset_id,
            "routineId": r.routine_id,
        },
        "routineType": r.routine_type,
        "language": r.language,
        "definitionBody": r.definition_body,
        "creationTime": str(int(r.creation_time.timestamp() * 1000)),
        "lastModifiedTime": str(int(r.last_modified_time.timestamp() * 1000)),
        "etag": r.etag,
    }
    if r.arguments:
        body["arguments"] = [_arg_to_rest(a) for a in r.arguments]
    if r.return_type is not None:
        body["returnType"] = r.return_type
    if r.imported_libraries:
        body["importedLibraries"] = list(r.imported_libraries)
    if r.description:
        body["description"] = r.description
    if r.determinism_level is not None:
        body["determinismLevel"] = r.determinism_level
    return body


def _parse_arguments(raw: list[dict[str, Any]]) -> tuple[RoutineArgument, ...]:
    out: list[RoutineArgument] = []
    for a in raw:
        name = a.get("name")
        if not name:
            raise InvalidQueryError("Routine argument missing name")
        out.append(
            RoutineArgument(
                name=name,
                argument_kind=a.get("argumentKind", "FIXED_TYPE"),
                mode=a.get("mode", "IN"),
                data_type=a.get("dataType"),
            ),
        )
    return tuple(out)


def _rest_to_routine_meta(
    project_id: str,
    dataset_id: str,
    body: dict[str, Any],
    clock: Any,
    existing: RoutineMeta | None = None,
) -> RoutineMeta:
    ref = body.get("routineReference", {})
    routine_id = ref.get("routineId") or (existing.routine_id if existing else "")
    if not routine_id:
        raise InvalidQueryError("Routine body must include routineReference.routineId")
    now = clock.now()

    args_raw = body.get("arguments")
    if args_raw is not None:
        args = _parse_arguments(args_raw)
    elif existing is not None:
        args = existing.arguments
    else:
        args = ()

    imported = body_or_existing(body, "importedLibraries", existing, "imported_libraries", [])

    return RoutineMeta(
        project_id=project_id,
        dataset_id=dataset_id,
        routine_id=routine_id,
        routine_type=body_or_existing(
            body, "routineType", existing, "routine_type", "SCALAR_FUNCTION"
        ),
        language=body_or_existing(body, "language", existing, "language", "SQL"),
        definition_body=body_or_existing(body, "definitionBody", existing, "definition_body", ""),
        arguments=args,
        return_type=body_or_existing(body, "returnType", existing, "return_type", None),
        imported_libraries=tuple(imported),
        description=body_or_existing(body, "description", existing, "description", None),
        determinism_level=body_or_existing(
            body, "determinismLevel", existing, "determinism_level", None
        ),
        creation_time=existing_attr_or(existing, "creation_time", now),
        last_modified_time=now,
        etag=generate_etag(project_id, dataset_id, routine_id, str(now)),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/datasets/{dataset_id}/routines")
def list_routines(
    project_id: str,
    dataset_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    routines = ctx.catalog.list_routines(project_id, dataset_id)
    return {
        "kind": "bigquery#listRoutinesResponse",
        "routines": [_routine_to_rest(r) for r in routines],
        "totalItems": len(routines),
    }


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/routines",
    status_code=status.HTTP_200_OK,
)
async def insert_routine(
    project_id: str,
    dataset_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    body = await request.json()
    meta = _rest_to_routine_meta(project_id, dataset_id, body, ctx.clock)

    if ctx.catalog.get_routine(project_id, dataset_id, meta.routine_id) is not None:
        raise resource_already_exists(
            ResourceRef("routine", project_id, dataset_id, meta.routine_id),
        )

    async with ctx.engine.write_lock():
        created = ctx.catalog.create_routine(meta)
        ctx.udf_registry.materialize(created, ctx.engine)
    return _routine_to_rest(created)


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/routines/{routine_id}",
)
def get_routine(
    project_id: str,
    dataset_id: str,
    routine_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    r = ctx.catalog.get_routine(project_id, dataset_id, routine_id)
    if r is None:
        raise resource_not_found(
            ResourceRef("routine", project_id, dataset_id, routine_id),
        )
    return _routine_to_rest(r)


@router.patch(
    "/projects/{project_id}/datasets/{dataset_id}/routines/{routine_id}",
)
async def patch_routine(
    project_id: str,
    dataset_id: str,
    routine_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    existing = ctx.catalog.get_routine(project_id, dataset_id, routine_id)
    if existing is None:
        raise resource_not_found(
            ResourceRef("routine", project_id, dataset_id, routine_id),
        )
    body = await request.json()
    body.setdefault("routineReference", {"routineId": routine_id})
    updated = _rest_to_routine_meta(project_id, dataset_id, body, ctx.clock, existing)
    async with ctx.engine.write_lock():
        result = ctx.catalog.update_routine(updated)
        ctx.udf_registry.materialize(result, ctx.engine)
    return _routine_to_rest(result)


@router.put(
    "/projects/{project_id}/datasets/{dataset_id}/routines/{routine_id}",
)
async def update_routine(
    project_id: str,
    dataset_id: str,
    routine_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    existing = ctx.catalog.get_routine(project_id, dataset_id, routine_id)
    if existing is None:
        raise resource_not_found(
            ResourceRef("routine", project_id, dataset_id, routine_id),
        )
    body = await request.json()
    body.setdefault("routineReference", {"routineId": routine_id})
    updated = _rest_to_routine_meta(project_id, dataset_id, body, ctx.clock, existing)
    async with ctx.engine.write_lock():
        result = ctx.catalog.update_routine(updated)
        ctx.udf_registry.materialize(result, ctx.engine)
    return _routine_to_rest(result)


@router.delete(
    "/projects/{project_id}/datasets/{dataset_id}/routines/{routine_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_routine(
    project_id: str,
    dataset_id: str,
    routine_id: str,
    ctx: _Ctx,
) -> Response:
    existing = ctx.catalog.get_routine(project_id, dataset_id, routine_id)
    if existing is None:
        raise resource_not_found(
            ResourceRef("routine", project_id, dataset_id, routine_id),
        )
    async with ctx.engine.write_lock():
        ctx.udf_registry.deregister(existing, ctx.engine)
        ctx.catalog.delete_routine(project_id, dataset_id, routine_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
