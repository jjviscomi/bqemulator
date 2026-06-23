"""Upload-host REST routes — multipart + resumable upload.

The official BigQuery client libraries (Python, Node, Go, Java) all
issue ``client.load_table_from_file(...)`` style calls through a
dedicated upload host (``https://bigquery.googleapis.com/upload/...``)
rather than the data-plane ``/bigquery/v2/projects/<p>/jobs`` endpoint
that handles ``load_table_from_uri``. This module hosts those routes.

Three upload protocols are supported, dispatched by the
``uploadType`` query parameter:

* ``media`` — single POST, body is the file content; the load
  configuration is supplied via query parameters.
* ``multipart`` — single POST, body is a ``multipart/related`` envelope
  containing exactly two parts: a JSON ``configuration.load`` block and
  the file bytes.
* ``resumable`` — initiation POST (returns the session URI in
  ``Location``), followed by one-or-more PUT chunks. The session is
  tracked by :class:`bqemulator.jobs.upload_session_manager.UploadSessionManager`.

The decoded media bytes are materialised to a per-session temp file
under ``Settings.upload_staging_dir`` and then passed to
:func:`bqemulator.jobs.executor.execute_load_job` via a synthesised
``file://`` ``sourceUris`` entry. The temp file is removed in a
``finally`` arm regardless of load outcome.

Reference docs:

* https://docs.cloud.google.com/bigquery/docs/loading-data-local
* https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs#parameters
"""

from __future__ import annotations

import email.message
import email.parser
import email.policy
import json
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from bqemulator.api.dependencies import AppContext, get_context
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import JobMeta
from bqemulator.domain.errors import (
    InvalidQueryError,
    UnsupportedFeatureError,
    ValidationError,
)
from bqemulator.jobs.executor import execute_load_job
from bqemulator.jobs.upload_session_manager import (
    ContentRangeError,
    UploadSession,
    UploadSessionManager,
    UploadSizeExceededError,
)

router = APIRouter(prefix="/upload/bigquery/v2", tags=["upload"])

_Ctx = Annotated[AppContext, Depends(get_context)]

#: BigQuery's documented set of valid upload types.
_VALID_UPLOAD_TYPES = frozenset({"media", "multipart", "resumable"})

#: Required number of parts in a ``multipart/related`` upload body
#: (positional: JSON config + media).
_MULTIPART_RELATED_PART_COUNT = 2

#: BigQuery returns ``308 Resume Incomplete`` on a partial chunk.
_HTTP_RESUME_INCOMPLETE = 308

#: Permitted Content-Type prefixes for the media part of a multipart
#: upload and the body of a media/resumable upload. The whitelist
#: matches BigQuery's documented load-source formats (CSV / NDJSON /
#: Parquet / Avro / ORC) plus the canonical generic forms BQ accepts.
_PERMITTED_MEDIA_CONTENT_TYPES = (
    "application/octet-stream",
    "application/x-www-form-urlencoded",
    "application/json",
    "application/vnd.bigquery.json",
    "text/csv",
    "text/plain",
    "application/avro",
    "application/vnd.apache.avro",
    "application/x-parquet",
    "application/vnd.apache.parquet",
    "application/x-orc",
    "application/vnd.apache.orc",
)


def _coerce_manager(ctx: AppContext) -> UploadSessionManager:
    """Return the upload-session manager from the context, raising if missing."""
    manager = ctx.upload_sessions
    if manager is None:  # pragma: no cover — composition root always sets this
        raise UnsupportedFeatureError(
            "Upload endpoints require the upload-session manager to be wired in",
        )
    return manager


def _validate_upload_type(upload_type: str | None) -> str:
    """Validate the ``uploadType`` query param against the known set."""
    if not upload_type:
        raise ValidationError(
            "Missing required query parameter 'uploadType'. "
            "Expected one of: media, multipart, resumable.",
        )
    if upload_type not in _VALID_UPLOAD_TYPES:
        raise ValidationError(
            f"Unknown uploadType={upload_type!r}. Expected one of: media, multipart, resumable.",
        )
    return upload_type


def _build_job_response(
    project_id: str,
    job_id: str,
    job_meta: JobMeta,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Render a ``Job`` resource matching the data-plane shape."""
    result: dict[str, Any] = {
        "kind": "bigquery#job",
        "id": f"{project_id}:{job_id}",
        "jobReference": {"projectId": project_id, "jobId": job_id, "location": "US"},
        "configuration": configuration,
        "status": {"state": job_meta.state},
        "statistics": job_meta.statistics,
        "etag": job_meta.etag,
    }
    if job_meta.error_result:
        result["status"]["errorResult"] = job_meta.error_result
        result["status"]["errors"] = [job_meta.error_result]
    return result


def _new_job_id() -> str:
    """Mint a fresh job id, matching the data-plane convention."""
    return f"bqemu_{uuid4().hex[:12]}"


async def _read_body_capped(request: Request, *, cap: int) -> bytes:
    """Stream the body off the wire, aborting if it exceeds ``cap`` bytes.

    Using ``request.body()`` would let the whole body buffer in memory
    before we check the size — for hostile clients we want to fail
    fast. The ASGI receive channel yields the body as a sequence of
    ``http.request`` messages; we accumulate them into a bytearray and
    bail the moment the running total exceeds the cap. The check
    happens **before** the bytes touch the staging directory.
    """
    received = bytearray()
    more = True
    async for chunk in _iter_request_body(request):
        if len(received) + len(chunk) > cap:
            raise UploadSizeExceededError(
                declared=len(received) + len(chunk),
                cap=cap,
            )
        received.extend(chunk)
        more = bool(chunk)  # keep the unused-var pattern stable
    del more
    return bytes(received)


async def _iter_request_body(request: Request) -> Any:
    """Iterate the ASGI receive channel's body messages.

    Yields ``bytes`` for each ``http.request`` message. Stops when the
    final message reports ``more_body=False`` or the channel emits
    ``http.disconnect``.
    """
    receive = request.receive
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        if message["type"] != "http.request":
            continue
        body: bytes = message.get("body", b"") or b""
        if body:
            yield body
        if not message.get("more_body", False):
            return


# ---------------------------------------------------------------------------
# Multipart helpers
# ---------------------------------------------------------------------------


def _parse_json_part(part: email.message.Message) -> dict[str, Any]:
    """Decode the JSON envelope from a ``multipart/related`` first part.

    Validates the part's declared content-type contains ``json``, parses
    the payload, and confirms the result is a JSON object.
    """
    json_ct = (part.get_content_type() or "").lower()
    if "json" not in json_ct:
        raise InvalidQueryError(
            f"multipart upload's first part must be JSON; got {json_ct!r}",
        )
    try:
        envelope = json.loads(part.get_payload(decode=True) or b"{}")
    except json.JSONDecodeError as exc:
        raise InvalidQueryError(
            f"multipart upload's first part is not valid JSON: {exc}",
        ) from exc
    if not isinstance(envelope, dict):
        raise InvalidQueryError(
            "multipart upload's first part must be a JSON object",
        )
    return envelope


def _parse_media_part(part: email.message.Message) -> bytes:
    """Extract the media bytes from a ``multipart/related`` second part.

    Validates the part's declared content-type against the
    :data:`_PERMITTED_MEDIA_CONTENT_TYPES` whitelist so an upload
    can't be coerced into materialising an arbitrary MIME envelope.
    """
    media_ct = (part.get_content_type() or "").lower()
    if not _is_permitted_media_type(media_ct):
        raise InvalidQueryError(
            f"multipart upload's second part has unsupported Content-Type {media_ct!r}",
        )
    media_bytes = part.get_payload(decode=True) or b""
    if not isinstance(media_bytes, bytes):  # pragma: no cover — defensive
        media_bytes = bytes(media_bytes)
    return media_bytes


def _parse_multipart_related(content_type: str, body: bytes) -> tuple[dict[str, Any], bytes]:
    """Parse a ``multipart/related`` body into ``(load_config, media_bytes)``.

    Uses the stdlib :mod:`email` parser — the multipart-related media
    type is structurally identical to the multipart MIME framing the
    parser handles. The two parts are positional (not named like
    ``multipart/form-data``) so we walk them in declaration order.

    The first part must carry ``Content-Type: application/json`` (or
    a vendor JSON variant) and parse as the ``Job`` resource. The
    second part carries the media bytes; its declared Content-Type is
    validated against the whitelist :data:`_PERMITTED_MEDIA_CONTENT_TYPES`
    so the server can't be coerced into materialising an arbitrary
    MIME envelope inside the staging directory.
    """
    if not content_type.lower().startswith(("multipart/related", "multipart/mixed")):
        raise InvalidQueryError(
            f"multipart upload requires Content-Type: multipart/related; got {content_type!r}",
        )
    # The stdlib parser expects the headers + body together. Reconstruct
    # a minimal RFC 822 message: a single Content-Type header followed
    # by a blank line, then the body.
    header_line = f"Content-Type: {content_type}\r\n\r\n".encode()
    parser = email.parser.BytesParser(policy=email.policy.default)
    message = parser.parsebytes(header_line + body)
    if not message.is_multipart():
        raise InvalidQueryError("multipart upload body is not a multipart envelope")
    parts = list(message.iter_parts())
    if len(parts) != _MULTIPART_RELATED_PART_COUNT:
        raise InvalidQueryError(
            f"multipart upload requires exactly 2 parts; got {len(parts)}",
        )
    json_part, media_part = parts
    return _parse_json_part(json_part), _parse_media_part(media_part)


def _is_permitted_media_type(media_ct: str) -> bool:
    """Return True if ``media_ct`` matches an allowed media-part type."""
    return any(media_ct.startswith(prefix) for prefix in _PERMITTED_MEDIA_CONTENT_TYPES)


def _load_configuration_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Pull the ``configuration`` block out of a ``Job`` resource envelope.

    Accepts the canonical ``{"configuration": {"load": {...}}}`` shape
    that BigQuery's client libraries serialise. The envelope may also
    include a ``jobReference`` and other fields; we only look at
    ``configuration`` since the upload host generates the job id.
    """
    configuration = envelope.get("configuration")
    if not isinstance(configuration, dict):
        raise InvalidQueryError(
            "Upload envelope is missing the required 'configuration' field",
        )
    load = configuration.get("load")
    if not isinstance(load, dict):
        raise InvalidQueryError(
            "Upload envelope is missing the required 'configuration.load' field",
        )
    return configuration


# ---------------------------------------------------------------------------
# Load execution wrapper
# ---------------------------------------------------------------------------


async def _run_load(
    *,
    project_id: str,
    configuration: dict[str, Any],
    media_path: str,
    ctx: AppContext,
) -> tuple[str, JobMeta]:
    """Run a load job for an uploaded local file. Cleanup happens in ``finally``.

    Synthesises a ``file://`` URI for the staging path and overrides
    ``configuration.load.sourceUris`` so :func:`execute_load_job` reads
    from the materialised upload. Returns ``(job_id, JobMeta)``.
    """
    job_id = _new_job_id()
    load_block: dict[str, Any] = dict(configuration.get("load", {}))
    load_block["sourceUris"] = [f"file://{media_path}"]
    # Merge back so the rendered ``configuration`` reflects the
    # synthesised URI (matches BQ's wire echo).
    merged_configuration: dict[str, Any] = {**configuration, "load": load_block}

    try:
        job_meta = await execute_load_job(project_id, job_id, merged_configuration, ctx)
    except (UnsupportedFeatureError, InvalidQueryError, ValidationError):
        raise
    except Exception as exc:  # noqa: BLE001 — async envelope, mirror data-plane
        now = ctx.clock.now()
        job_meta = JobMeta(
            project_id=project_id,
            job_id=job_id,
            job_type="LOAD",
            state="DONE",
            configuration=merged_configuration,
            statistics={"load": {"inputFiles": "1"}},
            error_result={
                "reason": "invalid",
                "message": f"Error while reading data, error message: {exc}",
                "location": f"file://{media_path}",
            },
            creation_time=now,
            start_time=now,
            end_time=now,
            etag=generate_etag(project_id, job_id, str(now)),
        )
    ctx.catalog.upsert_job(job_meta)
    return job_id, job_meta


# ---------------------------------------------------------------------------
# Route: POST /projects/{p}/jobs  (uploadType=media|multipart|resumable init)
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/jobs")
async def upload_initiate(
    project_id: str,
    request: Request,
    ctx: _Ctx,
    uploadType: str | None = Query(default=None, alias="uploadType"),  # noqa: N803
) -> Response:
    """Initiate a media / multipart / resumable upload.

    Dispatch:

    * ``uploadType=media`` — body is the file content; load
      configuration must be supplied via query parameters
      ``destinationTable``, ``sourceFormat``, etc. BigQuery itself
      rejects ``media`` for load jobs (multipart/resumable only), so
      the emulator mirrors the rejection.
    * ``uploadType=multipart`` — body is a ``multipart/related``
      envelope; the JSON part carries the load configuration, the
      media part carries the file bytes.
    * ``uploadType=resumable`` — body is the JSON ``Job`` resource
      (load configuration), media bytes follow on a subsequent PUT.
      Response is HTTP 200 with the session URI in the ``Location``
      header and ``X-GUploader-UploadID``.
    """
    upload_type = _validate_upload_type(uploadType)
    manager = _coerce_manager(ctx)

    if upload_type == "media":
        # BigQuery rejects ``uploadType=media`` for ``jobs.insert``. The
        # documented load path is multipart or resumable; we mirror the
        # rejection so client libraries fall back to the supported
        # protocol rather than silently encoding a bespoke shape.
        raise ValidationError(
            "uploadType=media is not supported for jobs.insert. "
            "Use multipart or resumable instead.",
        )

    if upload_type == "multipart":
        return await _handle_multipart(
            project_id=project_id,
            request=request,
            ctx=ctx,
            manager=manager,
        )

    # resumable initiation
    return await _handle_resumable_initiate(project_id=project_id, request=request, manager=manager)


async def _handle_multipart(
    *,
    project_id: str,
    request: Request,
    ctx: AppContext,
    manager: UploadSessionManager,
) -> Response:
    """Decode a multipart-related body, run the load, return the Job resource."""
    content_type = request.headers.get("content-type", "")
    body = await _read_body_capped(request, cap=manager.max_bytes)
    envelope, media_bytes = _parse_multipart_related(content_type, body)
    configuration = _load_configuration_from_envelope(envelope)

    if len(media_bytes) > manager.max_bytes:
        raise UploadSizeExceededError(declared=len(media_bytes), cap=manager.max_bytes)

    # Stage the media bytes into a temp file so the load executor can
    # read them via the existing file:// URI resolver. The path is
    # under the staging dir which is owned by the manager; cleanup
    # happens in the ``finally`` arm whether the load succeeds or fails.
    session = manager.create(project_id, configuration)
    try:
        with session.staging_path.open("wb") as fh:
            fh.write(media_bytes)
        session.received_bytes = len(media_bytes)
        job_id, job_meta = await _run_load(
            project_id=project_id,
            configuration=configuration,
            media_path=str(session.staging_path),
            ctx=ctx,
        )
    finally:
        manager.remove(session.session_id)

    return JSONResponse(
        status_code=200,
        content=_build_job_response(project_id, job_id, job_meta, job_meta.configuration),
    )


async def _handle_resumable_initiate(
    *,
    project_id: str,
    request: Request,
    manager: UploadSessionManager,
) -> Response:
    """Allocate a session and return the upload URI in ``Location``."""
    try:
        envelope = await request.json()
    except json.JSONDecodeError as exc:
        raise InvalidQueryError(
            f"Resumable initiation body is not valid JSON: {exc}",
        ) from exc
    if not isinstance(envelope, dict):
        raise InvalidQueryError(
            "Resumable initiation body must be a JSON object",
        )
    configuration = _load_configuration_from_envelope(envelope)
    session = manager.create(project_id, configuration)

    # Build the session URI the client should PUT chunks to. BigQuery
    # returns an absolute URI with the upload host; we mirror that by
    # echoing the inbound request's authority so test clients can
    # follow the Location header against the same emulator URL they
    # used to initiate.
    base_url = _request_authority(request)
    location = (
        f"{base_url}/upload/bigquery/v2/projects/{project_id}/jobs"
        f"?uploadType=resumable&upload_id={session.session_id}"
    )
    return Response(
        status_code=200,
        content=b"",
        media_type="text/plain",
        headers={
            "Location": location,
            "X-GUploader-UploadID": session.session_id,
        },
    )


def _request_authority(request: Request) -> str:
    """Return the ``scheme://host:port`` prefix for the inbound request."""
    parts = urlsplit(str(request.url))
    return f"{parts.scheme}://{parts.netloc}"


# ---------------------------------------------------------------------------
# Route: PUT /projects/{p}/jobs?upload_id=...  (resumable chunk upload)
# ---------------------------------------------------------------------------


@router.put("/projects/{project_id}/jobs")
async def upload_chunk(
    project_id: str,
    request: Request,
    ctx: _Ctx,
    upload_id: str = Query(default="", alias="upload_id"),
) -> Response:
    """Append a chunk to a resumable session or report its status.

    The client may set ``Content-Range`` to:

    * ``bytes <start>-<end>/<total>`` — normal chunk; the server
      returns ``308 Resume Incomplete`` until the final byte arrives,
      then ``200`` with the ``Job`` resource.
    * ``bytes <start>-<end>/*`` — chunk-of-unknown-total; the
      client signals completion later via a final PUT carrying a
      concrete total.
    * ``bytes */<total>`` — status probe (no body attached); the
      server returns the current offset in a ``Range`` response header.

    Out-of-order chunks, ``upload_id`` not matching the session id
    pattern, or size-cap overruns all surface as documented BigQuery
    error envelopes via the DomainError handler.
    """
    if not upload_id:
        raise ValidationError("Missing required query parameter 'upload_id'")
    manager = _coerce_manager(ctx)

    content_range = request.headers.get("content-range")
    body = await _read_body_capped(request, cap=manager.max_bytes)

    # Status probe: ``Content-Range: bytes */<total>`` and zero-length
    # body. The server returns 308 with the byte range received so far.
    if content_range and content_range.strip().startswith("bytes */"):
        session = manager.status(upload_id)
        return _resume_incomplete_response(session)

    try:
        session, complete = manager.append(upload_id, body, content_range=content_range)
    except ContentRangeError as exc:
        # Out-of-order or malformed range — surface as 400.
        raise InvalidQueryError(str(exc)) from exc

    if not complete:
        return _resume_incomplete_response(session)

    # Final chunk received; run the load and clean up.
    try:
        job_id, job_meta = await _run_load(
            project_id=project_id,
            configuration=session.configuration,
            media_path=str(session.staging_path),
            ctx=ctx,
        )
    finally:
        manager.remove(upload_id)

    return JSONResponse(
        status_code=200,
        content=_build_job_response(project_id, job_id, job_meta, job_meta.configuration),
    )


def _resume_incomplete_response(session: UploadSession) -> Response:
    """Return a ``308 Resume Incomplete`` with a correct ``Range`` header."""
    if session.received_bytes == 0:
        # BigQuery omits the Range header when nothing has been
        # received yet — mirror that so the client doesn't see a
        # spurious ``Range: bytes=0--1``.
        return Response(status_code=_HTTP_RESUME_INCOMPLETE, content=b"")
    upper = session.received_bytes - 1
    return Response(
        status_code=_HTTP_RESUME_INCOMPLETE,
        content=b"",
        headers={"Range": f"bytes=0-{upper}"},
    )


__all__ = ["router"]
