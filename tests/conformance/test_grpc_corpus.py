"""Parametrised gRPC-shape conformance runner (P3.d).

Every fixture under ``grpc_corpus/`` becomes one parametrised test:

1. Optional ``setup.sql`` runs via the BigQuery Python client (same
   path the SQL corpus exercises) to seed the table.
2. Optional ``setup_requests.json`` runs via :mod:`httpx` against the
   emulator REST endpoint, capturing variables into the placeholder
   context (same shape as the HTTP corpus).
3. The canonical ``request.json`` carries an ordered list of gRPC
   calls. The runner opens an insecure gRPC channel to
   ``bqemu_endpoint.grpc_endpoint`` and issues every call in order,
   substituting ``${VAR}`` placeholders captured from earlier calls.
4. The actual responses are diffed against
   ``expected_response.json`` using
   :mod:`tests.conformance._grpc_comparison` (structural-subset
   matching with the ``WILDCARD`` sentinel for opaque values like
   Arrow IPC bytes, stream names, timestamps).

The runner uses raw ``channel.unary_unary`` / ``channel.unary_stream``
/ ``channel.stream_stream`` calls — the same shape the existing
integration suite uses — so we exercise the wire-format path the
emulator implements, not the official client's deserialised view.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
import uuid

import grpc
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
from tests.conformance._grpc_comparison import compare_grpc_calls
from tests.conformance._grpc_corpus import (
    GRPC_SERVICE_PATHS,
    GrpcCall,
    GrpcFixture,
    deserialize_response,
    discover_grpc_fixtures,
    expand_placeholders,
    expand_placeholders_in_json,
    resolve_dotted_path,
    serialize_request,
)
from tests.conformance._http_corpus import HttpRequest
from tests.conformance.divergences import KNOWN_DIVERGENCES

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.testing.fixtures import EmulatorEndpoint

#: HTTP success ceiling — any status code at or above this aborts
#: the setup chain.
_HTTP_SUCCESS_CEILING = 300

ALL_GRPC_FIXTURES = discover_grpc_fixtures()


def _parametrize_grpc_fixtures() -> list[pytest.param]:
    """Wrap every gRPC fixture in a ``pytest.param`` annotated with any xfail.

    Mirrors :func:`tests.conformance.test_corpus._parametrize_fixtures`:
    fixtures listed in :data:`KNOWN_DIVERGENCES` carry an
    ``xfail(strict=True)`` marker referencing the ADR or
    ``out-of-scope.md`` anchor that pins the divergence.
    """
    params: list[pytest.param] = []
    for fixture in ALL_GRPC_FIXTURES:
        marks: list[pytest.MarkDecorator] = []
        divergence = KNOWN_DIVERGENCES.get(fixture.id)
        if divergence is not None:
            marks.append(pytest.mark.xfail(strict=True, reason=divergence))
        params.append(pytest.param(fixture, id=fixture.id, marks=marks))
    return params


@pytest.mark.parametrize("fixture", _parametrize_grpc_fixtures())
def test_grpc_conformance_fixture(
    fixture: GrpcFixture,
    bqemu_endpoint: EmulatorEndpoint,
) -> None:
    """Replay a gRPC fixture against the emulator and diff vs the baseline."""
    assert fixture.expected is not None  # discovery filters out unrecorded fixtures
    project = bqemu_endpoint.project_id

    dataset_fqdn: str | None = None
    rest_created_datasets: list[tuple[str, str]] = []
    captured: dict[str, str] = {}

    if fixture.needs_dataset:
        dataset_name = f"bqemu_grpcfx_{uuid.uuid4().hex[:12]}"
        dataset_fqdn = f"{project}.{dataset_name}"

    ctx = PlaceholderContext(
        dataset=dataset_fqdn or f"{project}.bqemu_unused",
        principal=DEFAULT_RUNNER_PRINCIPAL,
        group=DEFAULT_RUNNER_GROUP,
        other_principal=DEFAULT_RUNNER_OTHER_PRINCIPAL,
    )
    base_mapping = _placeholder_mapping(ctx)

    try:
        if dataset_fqdn is not None:
            _create_dataset(bqemu_endpoint.rest_url, dataset_fqdn)

        if fixture.setup_sql is not None:
            _run_setup_sql(
                rest_url=bqemu_endpoint.rest_url,
                project=project,
                setup_sql=substitute_placeholders(fixture.setup_sql, ctx),
            )

        with httpx.Client(base_url=bqemu_endpoint.rest_url, timeout=30.0) as http:
            for idx, setup_request in enumerate(fixture.setup_requests):
                response = _issue_http_request(
                    http,
                    setup_request,
                    mapping={**base_mapping, **captured},
                    source=f"{fixture.id} setup[#{idx}]",
                )
                if response.status_code >= _HTTP_SUCCESS_CEILING:
                    msg = (
                        f"{fixture.id} setup[#{idx}] {setup_request.method} "
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
                    captured.update(_apply_http_captures(setup_request.capture, response.json()))

        actual_calls: list[dict[str, Any]] = []
        with grpc.insecure_channel(bqemu_endpoint.grpc_endpoint) as channel:
            for idx, call in enumerate(fixture.request.calls):
                mapping = {**base_mapping, **captured}
                actual_call = _issue_grpc_call(
                    channel,
                    call,
                    mapping=mapping,
                    source=f"{fixture.id} grpc[#{idx}]",
                )
                actual_calls.append(actual_call)
                if call.capture and actual_call.get("response"):
                    captured.update(_apply_grpc_captures(call.capture, actual_call["response"]))
                elif call.capture and actual_call.get("responses"):
                    # Server-stream / bidi-stream captures apply to the
                    # FIRST response message (the only deterministic
                    # candidate — later messages may not exist on the
                    # emulator if it batches differently from real BQ).
                    first = actual_call["responses"][0] if actual_call["responses"] else None
                    if first is not None:
                        captured.update(_apply_grpc_captures(call.capture, first))

        # Forward-substitute placeholders in the expected baseline so
        # the recorded `${PROJECT}` / `${DATASET_ID}` / `${DATASET}`
        # markers expand to the runner-time identities. The recorder
        # writes the reverse form (real-BQ project → ${PROJECT}) so
        # the same fixture file is portable across environments.
        expanded_expected = _expand_expected_calls(
            list(fixture.expected.calls),
            mapping={**base_mapping, **captured},
        )
        report = compare_grpc_calls(
            expected=expanded_expected,
            actual=actual_calls,
        )
        if not report.ok:
            diagnostic = "\n".join(report.diffs)
            pytest.fail(f"gRPC conformance diff for {fixture.id}:\n{diagnostic}")
    finally:
        for proj_id, ds_id in reversed(rest_created_datasets):
            _delete_dataset(bqemu_endpoint.rest_url, f"{proj_id}.{ds_id}")
        if dataset_fqdn is not None:
            _delete_dataset(bqemu_endpoint.rest_url, dataset_fqdn)


def _issue_grpc_call(
    channel: Any,
    call: GrpcCall,
    *,
    mapping: dict[str, str],
    source: str,
) -> dict[str, Any]:
    """Issue one gRPC call and return its outcome as a JSON-shaped dict."""
    service_name, method_name = call.method.split(".", 1)
    service_path = GRPC_SERVICE_PATHS[service_name]
    rpc_path = f"{service_path}/{method_name}"

    try:
        if call.kind == "bidi_stream":
            expanded_requests = [expand_placeholders_in_json(req, mapping) for req in call.requests]
            request_payloads = [
                serialize_request(call.method, _ensure_dict(req)) for req in expanded_requests
            ]
        else:
            expanded_request = (
                expand_placeholders_in_json(call.request, mapping)
                if call.request is not None
                else {}
            )
            request_payload = serialize_request(call.method, _ensure_dict(expanded_request))
    except (KeyError, ValueError) as exc:
        msg = f"{source}: placeholder expansion or serialization failed: {exc}"
        raise RuntimeError(msg) from exc

    outcome: dict[str, Any] = {"method": call.method, "status": "OK"}
    try:
        if call.kind == "unary":
            response_bytes = channel.unary_unary(rpc_path)(request_payload)
            outcome["response"] = deserialize_response(call.method, response_bytes)
        elif call.kind == "server_stream":
            outcome["responses"] = [
                deserialize_response(call.method, resp_bytes)
                for resp_bytes in channel.unary_stream(rpc_path)(request_payload)
            ]
        elif call.kind == "bidi_stream":

            def _iter_requests() -> Any:
                yield from request_payloads

            outcome["responses"] = [
                deserialize_response(call.method, resp_bytes)
                for resp_bytes in channel.stream_stream(rpc_path)(_iter_requests())
            ]
        else:  # pragma: no cover — defensive
            msg = f"{source}: unsupported kind {call.kind!r}"
            raise RuntimeError(msg)
    except grpc.RpcError as exc:
        # gRPC's sync handlers raise a single RpcError on the channel
        # iterator when the status comes back non-OK. The exception
        # carries .code() (grpc.StatusCode) and .details() (str).
        code = exc.code() if hasattr(exc, "code") else grpc.StatusCode.UNKNOWN
        outcome["status"] = code.name if code is not None else "UNKNOWN"
        outcome["error_message"] = exc.details() if hasattr(exc, "details") else str(exc)

    return outcome


def _expand_expected_calls(
    calls: list,
    *,
    mapping: dict[str, str],
) -> list:
    """Forward-substitute placeholders in the recorded baseline.

    The recorder writes ``${PROJECT}`` / ``${DATASET_ID}`` /
    ``${DATASET}`` markers in place of the recording-time identity
    strings so the baseline is portable. The runner expands them
    back to the runtime identities before diffing against the actual
    emulator response.
    """
    from tests.conformance._grpc_corpus import GrpcExpectedCall

    expanded: list[GrpcExpectedCall] = []
    for call in calls:
        response = (
            expand_placeholders_in_json(call.response, mapping)
            if call.response is not None
            else None
        )
        responses: tuple[dict[str, Any], ...] | None
        if call.responses is not None:
            responses = tuple(
                _ensure_dict(expand_placeholders_in_json(r, mapping)) for r in call.responses
            )
        else:
            responses = None
        error_message = (
            expand_placeholders(call.error_message, mapping)
            if call.error_message is not None
            else None
        )
        expanded.append(
            GrpcExpectedCall(
                method=call.method,
                status=call.status,
                response=_ensure_dict(response) if response is not None else None,
                responses=responses,
                error_message=error_message,
            )
        )
    return expanded


def _ensure_dict(value: object) -> dict[str, Any]:
    """Narrow a placeholder-expanded value to a dict for proto serialization.

    ``expand_placeholders_in_json`` is typed as returning ``object``; the
    framework guarantees the top-level shape is a dict for every gRPC
    request payload. A runtime check turns a structural bug into a
    loud, debuggable error rather than silently passing the wrong type
    to proto-plus.
    """
    if not isinstance(value, dict):
        msg = f"expected request payload to be a dict, got {type(value).__name__}"
        raise TypeError(msg)
    return value


def _apply_http_captures(
    captures: tuple[tuple[str, str], ...],
    body: object,
) -> dict[str, str]:
    """Resolve every capture entry against a JSON body."""
    out: dict[str, str] = {}
    for name, dotted in captures:
        value = resolve_dotted_path(body, dotted)
        if value is None:
            msg = f"capture {name!r}: path {dotted!r} resolved to None"
            raise ValueError(msg)
        out[name] = str(value)
    return out


def _apply_grpc_captures(
    captures: tuple[tuple[str, str], ...],
    response: dict[str, Any],
) -> dict[str, str]:
    """Resolve every capture entry against a gRPC response dict."""
    out: dict[str, str] = {}
    for name, dotted in captures:
        value = resolve_dotted_path(response, dotted)
        if value is None:
            msg = f"capture {name!r}: path {dotted!r} resolved to None"
            raise ValueError(msg)
        out[name] = str(value)
    return out


def _placeholder_mapping(ctx: PlaceholderContext) -> dict[str, str]:
    """Base placeholder mapping shared with the SQL + HTTP corpora."""
    return {
        "DATASET": ctx.dataset,
        "PROJECT": ctx.project,
        "DATASET_ID": ctx.dataset_id,
        "PRINCIPAL": ctx.principal,
        "GROUP": ctx.group,
        "OTHER_PRINCIPAL": ctx.other_principal,
    }


def _issue_http_request(
    http: httpx.Client,
    request: HttpRequest,
    *,
    mapping: dict[str, str],
    source: str,
) -> httpx.Response:
    """Substitute placeholders in an HTTP request and issue it."""
    try:
        path = expand_placeholders(request.path, mapping)
        body = (
            expand_placeholders_in_json(request.body, mapping) if request.body is not None else None
        )
        headers = {name: expand_placeholders(value, mapping) for name, value in request.headers}
    except (KeyError, ValueError) as exc:
        msg = f"{source}: placeholder expansion failed: {exc}"
        raise RuntimeError(msg) from exc
    return http.request(request.method, path, json=body, headers=headers or None)


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
    """Run the fixture's ``setup.sql`` against the emulator via ``jobs.query``."""
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
