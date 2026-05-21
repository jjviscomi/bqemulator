"""Parametrised HTTP-shape conformance runner (P2.f).

Every fixture under ``http_corpus/`` becomes one parametrised test.
The fixture's optional ``setup.sql`` runs through the BigQuery Python
client (so the same code path the SQL corpus exercises seeds the
table); the fixture's optional ``setup_requests.json`` runs via
:mod:`httpx` against the emulator base URL, capturing variables into
the placeholder context; the canonical ``request.json`` is then issued
and its response is diffed against ``expected_response.json``.

The runner uses :class:`httpx.Client` rather than the BigQuery Python
client because the HTTP corpus exercises the wire format directly —
the goal is to assert what the emulator returns at HTTP level, not
the client's deserialised view of it.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

import httpx
import pytest

from tests.conformance._corpus import (
    DEFAULT_RUNNER_GROUP,
    DEFAULT_RUNNER_OTHER_PRINCIPAL,
    DEFAULT_RUNNER_PRINCIPAL,
    PlaceholderContext,
    split_statements,
    substitute_placeholders,
)
from tests.conformance._http_comparison import compare_http_response
from tests.conformance._http_corpus import (
    HttpFixture,
    HttpRequest,
    discover_http_fixtures,
    expand_placeholders,
    expand_placeholders_in_json,
    resolve_dotted_path,
)
from tests.conformance.conftest import _G1_RECORDED_BUCKET
from tests.conformance.divergences import KNOWN_DIVERGENCES

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.testing.fixtures import EmulatorEndpoint

#: HTTP success ceiling. Any status code at or above this value
#: aborts the setup chain — with the exception of the documented
#: ``308 Resume Incomplete`` response that the upload-host resumable
#: chunk endpoint emits for non-final chunks (G2). A resumable setup
#: chain may issue several PUTs each returning 308 before the test's
#: canonical request (e.g. a status probe) fires.
_HTTP_SUCCESS_CEILING = 300
_HTTP_RESUME_INCOMPLETE = 308

ALL_HTTP_FIXTURES = discover_http_fixtures()


def _parametrize_http_fixtures() -> list[pytest.param]:
    """Wrap every HTTP fixture in a ``pytest.param`` annotated with any xfail.

    Mirrors :func:`tests.conformance.test_corpus._parametrize_fixtures`:
    HTTP fixtures listed in :data:`KNOWN_DIVERGENCES` carry an
    ``xfail(strict=True)`` marker referencing the ADR or
    ``out-of-scope.md`` anchor that pins the divergence.
    """
    params: list[pytest.param] = []
    for fixture in ALL_HTTP_FIXTURES:
        marks: list[pytest.MarkDecorator] = []
        divergence = KNOWN_DIVERGENCES.get(fixture.id)
        if divergence is not None:
            marks.append(pytest.mark.xfail(strict=True, reason=divergence))
        params.append(pytest.param(fixture, id=fixture.id, marks=marks))
    return params


@pytest.mark.parametrize("fixture", _parametrize_http_fixtures())
def test_http_conformance_fixture(
    fixture: HttpFixture,
    bqemu_endpoint: EmulatorEndpoint,
) -> None:
    """Replay an HTTP fixture against the emulator and diff vs the baseline.

    The body of the test follows the SQL conformance shape: optional
    setup → canonical request → diff. The diff uses structural-subset
    matching (see :mod:`tests.conformance._http_comparison`).
    """
    assert fixture.expected is not None  # discovery filters out unrecorded fixtures
    project = bqemu_endpoint.project_id

    dataset_fqdn: str | None = None
    rest_created_datasets: list[tuple[str, str]] = []
    captured: dict[str, str] = {}

    if fixture.needs_dataset:
        dataset_name = f"bqemu_httpfx_{uuid.uuid4().hex[:12]}"
        dataset_fqdn = f"{project}.{dataset_name}"

    ctx = PlaceholderContext(
        dataset=dataset_fqdn or f"{project}.bqemu_unused",
        principal=DEFAULT_RUNNER_PRINCIPAL,
        group=DEFAULT_RUNNER_GROUP,
        other_principal=DEFAULT_RUNNER_OTHER_PRINCIPAL,
        # G1: match the recorder's BQEMU_CONFORMANCE_GCS_BUCKET value so
        # ``${GCS_BUCKET}`` in load/extract fixtures substitutes to the
        # same string the baseline embedded in sourceUris.
        gcs_bucket=_G1_RECORDED_BUCKET,
    )
    base_mapping = _placeholder_mapping(ctx)

    try:
        if dataset_fqdn is not None:
            _create_dataset(bqemu_endpoint.rest_url, dataset_fqdn)

        if fixture.setup_sql is not None:
            assert dataset_fqdn is not None  # narrowing for mypy
            _run_setup_sql(
                rest_url=bqemu_endpoint.rest_url,
                project=project,
                setup_sql=substitute_placeholders(fixture.setup_sql, ctx),
            )

        with httpx.Client(base_url=bqemu_endpoint.rest_url, timeout=30.0) as http:
            _replay_setup_requests(
                http,
                setup_requests=fixture.setup_requests,
                base_mapping=base_mapping,
                captured=captured,
                rest_created_datasets=rest_created_datasets,
                fixture_id=fixture.id,
                fixture_dir=fixture.path,
            )

            # Canonical request. Substitute every placeholder (base +
            # captures) and issue. The response is diffed against the
            # recorded baseline; the runner aggregates every diff so an
            # operator can triage multiple shape regressions at once.
            response = _issue_request(
                http,
                fixture.request,
                mapping={**base_mapping, **captured},
                source=f"{fixture.id} canonical",
                fixture_dir=fixture.path,
            )
            actual_body: object
            if response.content:
                try:
                    actual_body = response.json()
                except json.JSONDecodeError:
                    actual_body = response.text
            else:
                actual_body = ""

        report = compare_http_response(
            expected_status=fixture.expected.http_status,
            expected_body=fixture.expected.body,
            expected_headers=fixture.expected.headers,
            actual_status=response.status_code,
            actual_body=actual_body,
            actual_headers=dict(response.headers),
        )
        if not report.ok:
            diagnostic = "\n".join(report.diffs)
            pytest.fail(f"HTTP conformance diff for {fixture.id}:\n{diagnostic}")
    finally:
        for proj_id, ds_id in reversed(rest_created_datasets):
            _delete_dataset(bqemu_endpoint.rest_url, f"{proj_id}.{ds_id}")
        if dataset_fqdn is not None:
            _delete_dataset(bqemu_endpoint.rest_url, dataset_fqdn)


def _placeholder_mapping(ctx: PlaceholderContext) -> dict[str, str]:
    """Return the base placeholder mapping shared with the SQL corpus."""
    return {
        "DATASET": ctx.dataset,
        "PROJECT": ctx.project,
        "DATASET_ID": ctx.dataset_id,
        "PRINCIPAL": ctx.principal,
        "GROUP": ctx.group,
        "OTHER_PRINCIPAL": ctx.other_principal,
        "GCS_BUCKET": ctx.gcs_bucket,
    }


def _replay_setup_requests(
    http: httpx.Client,
    *,
    setup_requests: Sequence[HttpRequest],
    base_mapping: dict[str, str],
    captured: dict[str, str],
    rest_created_datasets: list[tuple[str, str]],
    fixture_id: str,
    fixture_dir: Path,
) -> None:
    """Issue each setup request in order, capturing variables for later substitution.

    Mutates ``captured`` and ``rest_created_datasets`` in place — the
    runner uses both to track state that the canonical request depends
    on (header-captured upload IDs, datasets created via ``POST /datasets``
    that the test teardown must delete).
    """
    for idx, setup_request in enumerate(setup_requests):
        response = _issue_request(
            http,
            setup_request,
            mapping={**base_mapping, **captured},
            source=f"{fixture_id} setup[#{idx}]",
            fixture_dir=fixture_dir,
        )
        if (
            response.status_code >= _HTTP_SUCCESS_CEILING
            and response.status_code != _HTTP_RESUME_INCOMPLETE
        ):
            msg = (
                f"{fixture_id} setup[#{idx}] {setup_request.method} "
                f"{setup_request.path} returned {response.status_code}: "
                f"{response.text}"
            )
            raise RuntimeError(msg)
        tracked = _track_dataset_creation(
            setup_request.method,
            expand_placeholders(setup_request.path, {**base_mapping, **captured}),
            response,
        )
        if tracked is not None:
            rest_created_datasets.append(tracked)
        if setup_request.capture:
            body_json: object
            if response.content:
                try:
                    body_json = response.json()
                except json.JSONDecodeError:
                    body_json = {}
            else:
                body_json = {}
            captured.update(
                _apply_captures(
                    setup_request.capture,
                    body_json,
                    response_headers=dict(response.headers),
                )
            )


def _issue_request(
    http: httpx.Client,
    request: HttpRequest,
    *,
    mapping: dict[str, str],
    source: str,
    fixture_dir: object | None = None,
) -> httpx.Response:
    """Substitute placeholders in ``request`` and issue against ``http``.

    When ``request.body_bin`` is set the runner reads that sibling
    filename under ``fixture_dir`` as raw bytes and POSTs them as the
    request body (preserving the headers verbatim — multipart envelopes
    and file payloads bypass JSON serialisation entirely).
    """
    try:
        path = expand_placeholders(request.path, mapping)
        body_json = (
            expand_placeholders_in_json(request.body, mapping) if request.body is not None else None
        )
        headers = {name: expand_placeholders(value, mapping) for name, value in request.headers}
    except (KeyError, ValueError) as exc:
        msg = f"{source}: placeholder expansion failed: {exc}"
        raise RuntimeError(msg) from exc

    if request.body_bin is not None:
        if fixture_dir is None:
            msg = f"{source}: body_bin requires a fixture directory"
            raise RuntimeError(msg)
        body_path = fixture_dir / request.body_bin  # type: ignore[operator]
        body_bytes = body_path.read_bytes()
        body_bytes = _substitute_placeholders_in_bytes(body_bytes, mapping)
        return http.request(
            request.method,
            path,
            content=body_bytes,
            headers=headers or None,
        )

    return http.request(request.method, path, json=body_json, headers=headers or None)


def _substitute_placeholders_in_bytes(body: bytes, mapping: dict[str, str]) -> bytes:
    """Replace ``${TOKEN}`` byte sequences inside a binary body.

    G2 multipart / resumable upload fixtures carry a binary body with
    references to ``${DATASET_ID}`` / ``${PROJECT}`` inside the JSON
    part. The body is binary as a whole (the media part may be raw
    Parquet / Avro / Arrow), so we substitute placeholders as byte
    sequences rather than going through JSON serialisation.
    """
    for token, value in mapping.items():
        body = body.replace(f"${{{token}}}".encode(), value.encode())
    return body


def _apply_captures(
    captures: tuple[tuple[str, str], ...],
    body: object,
    *,
    response_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve every capture entry against ``body`` and return as a string-map.

    A dotted-path starting with ``header:`` reads the named response
    header instead of the JSON body — used by G2 resumable fixtures
    where the session id arrives in ``X-GUploader-UploadID`` /
    ``Location`` rather than the body. Header names are matched
    case-insensitively.
    """
    out: dict[str, str] = {}
    headers_lower: dict[str, str] = {
        name.lower(): value for name, value in (response_headers or {}).items()
    }
    for name, dotted in captures:
        if dotted.startswith("header:"):
            header_name = dotted[len("header:") :].lower()
            if header_name not in headers_lower:
                msg = f"capture {name!r}: response header {dotted[len('header:') :]!r} absent"
                raise ValueError(msg)
            out[name] = headers_lower[header_name]
            continue
        value = resolve_dotted_path(body, dotted)
        if value is None:
            msg = f"capture {name!r}: path {dotted!r} resolved to None"
            raise ValueError(msg)
        # Captures land inside URL paths and JSON bodies; coerce
        # everything to a string so the substitution machinery treats
        # them uniformly. The recorder writes BQ job ids as strings
        # already, so this is a no-op for the common case.
        out[name] = str(value)
    return out


def _create_dataset(rest_url: str, dataset_fqdn: str) -> None:
    """Create a temp dataset on the emulator via the REST endpoint."""
    project, dataset_id = dataset_fqdn.split(".", 1)
    with httpx.Client(base_url=rest_url, timeout=30.0) as http:
        response = http.post(
            f"/bigquery/v2/projects/{project}/datasets",
            json={"datasetReference": {"projectId": project, "datasetId": dataset_id}},
        )
        response.raise_for_status()


def _delete_dataset(rest_url: str, dataset_fqdn: str) -> None:
    """Delete a temp dataset on the emulator via the REST endpoint."""
    project, dataset_id = dataset_fqdn.split(".", 1)
    with httpx.Client(base_url=rest_url, timeout=30.0) as http:
        http.delete(
            f"/bigquery/v2/projects/{project}/datasets/{dataset_id}",
            params={"deleteContents": "true"},
        )


def _run_setup_sql(*, rest_url: str, project: str, setup_sql: str) -> None:
    """Run the fixture's ``setup.sql`` against the emulator via ``jobs.query``.

    Mirrors the SQL conformance runner: each ``;``-separated statement
    is submitted in order to ``POST /projects/<p>/queries`` so the
    same code path that the BigQuery Python client uses runs.
    """
    with httpx.Client(base_url=rest_url, timeout=60.0) as http:
        for stmt in split_statements(setup_sql):
            response = http.post(
                f"/bigquery/v2/projects/{project}/queries",
                json={"query": stmt, "useLegacySql": False},
            )
            response.raise_for_status()


def _track_dataset_creation(
    method: str, path: str, response: httpx.Response
) -> tuple[str, str] | None:
    """Detect ``POST /projects/<p>/datasets`` and return the (project, id)."""
    if method.upper() != "POST":
        return None
    if "/datasets" not in path:
        return None
    try:
        body = response.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    ref = body.get("datasetReference")
    if not isinstance(ref, dict):
        return None
    project = ref.get("projectId")
    dataset_id = ref.get("datasetId")
    if not isinstance(project, str) or not isinstance(dataset_id, str):
        return None
    return project, dataset_id
