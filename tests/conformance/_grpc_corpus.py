"""gRPC-shape conformance corpus — discovery + per-fixture bundles.

The gRPC corpus lives at ``tests/conformance/grpc_corpus/`` and is a
sibling of ``sql_corpus/`` (row+schema diff) and ``http_corpus/``
(REST body structural-subset diff). Each fixture exercises a gRPC
wire-format shape on the BigQuery Storage Read API
(``BigQueryRead``) or the Storage Write API (``BigQueryWrite``) that
the integration suite's deserialised-Python-object assertions cannot
express. Locked by P3.d.

Fixture layout::

    tests/conformance/grpc_corpus/<phase>/<name>/
        setup.sql               # optional — table seed run via REST before any gRPC call
        setup_requests.json     # optional — ordered REST calls run pre-canonical
        request.json            # canonical gRPC call sequence (one or more chained)
        expected_response.json  # recorded baseline (status + messages per call)

``setup.sql`` and ``setup_requests.json`` use the same shape as the
HTTP corpus (see :mod:`tests.conformance._http_corpus`). They run via
the BigQuery REST client / ``httpx`` so they exercise the same
catalog setup path the runner uses for the SQL + HTTP corpora.

``request.json`` carries an ordered list of gRPC calls. Each call:

- ``method`` — fully-qualified service.method (e.g.
  ``BigQueryRead.CreateReadSession``,
  ``BigQueryWrite.AppendRows``).
- ``kind`` — one of ``unary``, ``server_stream``, ``bidi_stream``.
  The recorder + runner infer this from the method when omitted.
- ``request`` (unary / server_stream) — a JSON object the framework
  deserialises into the method's request proto via
  ``proto.Message.from_json``.
- ``requests`` (bidi_stream) — an ordered list of JSON objects the
  framework deserialises into the method's request proto.
- ``capture`` — optional dotted-path → variable-name map applied to
  the first response message so subsequent calls can substitute
  ``${VAR}`` placeholders.

``expected_response.json`` is the recorded baseline:

- ``calls`` — ordered list with one entry per call in ``request.json``.
  Each entry carries ``method``, ``status`` (gRPC status code name —
  ``OK`` / ``NOT_FOUND`` / etc.), and either:
   * ``response`` (single proto-as-JSON dict, unary call), or
   * ``responses`` (list of proto-as-JSON dicts, server/bidi
     streaming), or
   * ``error_message`` (free-form error detail, when status != OK).

The comparator runs **structural subset** matching on the response
bodies: every key in the recorded baseline must be present in the
emulator's response (unless the recorded value is the ``WILDCARD``
sentinel ``"<*>"``), but extra emulator-side keys are tolerated.
Opaque values that the emulator cannot reproduce bit-exact (Arrow
IPC byte payloads, generated stream / session names, server-
generated timestamps) get masked to ``WILDCARD`` at recording time.

Placeholder set is shared with the HTTP corpus
(``${PROJECT}`` / ``${DATASET}`` / ``${DATASET_ID}`` / ``${PRINCIPAL}`` /
``${GROUP}`` / ``${OTHER_PRINCIPAL}``) and extended at runtime with
the union of any ``capture``d response values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from tests.conformance._http_corpus import HttpRequest, _request_from_dict

# Repository-relative path: ``tests/conformance/grpc_corpus``. Mirrors
# the corresponding constants in :mod:`tests.conformance._corpus` /
# :mod:`tests.conformance._http_corpus`.
GRPC_CORPUS_DIR = Path(__file__).parent / "grpc_corpus"

# gRPC phase sub-directories. Order is stable so parametrised tests
# have predictable IDs.
GRPC_PHASE_SUBDIRS: tuple[str, ...] = ("storage_read", "storage_write")

# Sentinel for "any value is acceptable at this key". The recorder
# writes this where the recorded BigQuery response carried a server-
# generated opaque value (stream name, session id, timestamps,
# Arrow-IPC bytes, write-stream offsets). The comparator treats a
# key with this value as "must be present; value not checked".
WILDCARD = "<*>"

# Captured-variable placeholder pattern (same disjoint upper-token
# convention as the HTTP corpus).
_CAPTURE_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# gRPC method kinds. ``unary`` = single request → single response;
# ``server_stream`` = single request → stream of responses;
# ``bidi_stream`` = stream of requests → stream of responses.
GRPC_METHOD_KINDS = frozenset({"unary", "server_stream", "bidi_stream"})

# Default kind per fully-qualified ``<service>.<method>`` — used when
# the fixture omits ``kind``. The recorder and runner both read this
# so the fixture file can stay terse.
DEFAULT_METHOD_KIND: dict[str, str] = {
    "BigQueryRead.CreateReadSession": "unary",
    "BigQueryRead.ReadRows": "server_stream",
    "BigQueryRead.SplitReadStream": "unary",
    "BigQueryWrite.CreateWriteStream": "unary",
    "BigQueryWrite.AppendRows": "bidi_stream",
    "BigQueryWrite.GetWriteStream": "unary",
    "BigQueryWrite.FinalizeWriteStream": "unary",
    "BigQueryWrite.BatchCommitWriteStreams": "unary",
    "BigQueryWrite.FlushRows": "unary",
}

# Fully-qualified gRPC paths used on the wire.
GRPC_SERVICE_PATHS: dict[str, str] = {
    "BigQueryRead": "/google.cloud.bigquery.storage.v1.BigQueryRead",
    "BigQueryWrite": "/google.cloud.bigquery.storage.v1.BigQueryWrite",
}


@dataclass(slots=True, frozen=True)
class GrpcCall:
    """One gRPC call — part of a fixture's canonical sequence."""

    method: str  # "BigQueryRead.CreateReadSession" etc.
    kind: str  # "unary" / "server_stream" / "bidi_stream"
    request: dict[str, Any] | None  # unary / server_stream payload
    requests: tuple[dict[str, Any], ...]  # bidi_stream payload
    capture: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True, frozen=True)
class GrpcExpectedCall:
    """Recorded baseline for one gRPC call."""

    method: str
    status: str  # "OK" / "NOT_FOUND" / ...
    response: dict[str, Any] | None = None  # unary
    responses: tuple[dict[str, Any], ...] | None = None  # server / bidi stream
    error_message: str | None = None


@dataclass(slots=True, frozen=True)
class GrpcRequest:
    """The canonical sequence of gRPC calls a fixture issues."""

    calls: tuple[GrpcCall, ...]


@dataclass(slots=True, frozen=True)
class GrpcExpectedResponse:
    """Recorded baseline — one entry per call in the request sequence."""

    calls: tuple[GrpcExpectedCall, ...]


@dataclass(slots=True, frozen=True)
class GrpcFixture:
    """One gRPC conformance fixture on disk."""

    phase: str
    name: str
    path: Path
    setup_sql: str | None
    setup_requests: tuple[HttpRequest, ...]
    request: GrpcRequest
    expected_path: Path
    expected: GrpcExpectedResponse | None = field(default=None)

    @property
    def id(self) -> str:
        """Parametrize-friendly id, e.g. ``storage_read/sr_create_session_one_stream``."""
        return f"{self.phase}/{self.name}"

    @property
    def needs_dataset(self) -> bool:
        """True when the fixture has a ``setup.sql`` requiring a temp dataset."""
        return self.setup_sql is not None


def discover_grpc_fixtures(
    corpus_dir: Path | None = None,
    *,
    include_unrecorded: bool = False,
) -> list[GrpcFixture]:
    """Walk the gRPC corpus and return every fixture directory.

    A fixture directory is recognised by the presence of
    ``request.json``. By default fixtures without an
    ``expected_response.json`` are excluded so the runner only attempts
    to compare against recorded baselines; the recorder passes
    ``include_unrecorded=True`` to surface them.
    """
    root = corpus_dir or GRPC_CORPUS_DIR
    fixtures: list[GrpcFixture] = []
    for phase in GRPC_PHASE_SUBDIRS:
        phase_dir = root / phase
        if not phase_dir.is_dir():
            continue
        for entry in sorted(phase_dir.iterdir()):
            if not entry.is_dir():
                continue
            request_path = entry / "request.json"
            if not request_path.is_file():
                continue
            expected_path = entry / "expected_response.json"
            if not include_unrecorded and not expected_path.is_file():
                continue
            setup_sql_path = entry / "setup.sql"
            setup_sql = (
                setup_sql_path.read_text(encoding="utf-8") if setup_sql_path.is_file() else None
            )
            setup_requests = _load_setup_requests(entry / "setup_requests.json")
            request = _load_request(request_path)
            expected: GrpcExpectedResponse | None = None
            if expected_path.is_file():
                expected = _load_expected_response(expected_path)
            fixtures.append(
                GrpcFixture(
                    phase=phase,
                    name=entry.name,
                    path=entry,
                    setup_sql=setup_sql,
                    setup_requests=setup_requests,
                    request=request,
                    expected_path=expected_path,
                    expected=expected,
                )
            )
    return fixtures


def _load_setup_requests(path: Path) -> tuple[HttpRequest, ...]:
    """Read ``setup_requests.json`` as a tuple of ``HttpRequest`` entries.

    Reuses the HTTP corpus's loader so setup semantics stay identical.
    """
    if not path.is_file():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, list):
        msg = f"{path}: setup_requests.json must be a top-level list"
        raise TypeError(msg)
    out: list[HttpRequest] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            msg = f"{path}: entry #{idx} must be an object"
            raise TypeError(msg)
        out.append(_request_from_dict(item, source=f"{path}:#{idx}"))
    return tuple(out)


def _load_request(path: Path) -> GrpcRequest:
    """Read ``request.json`` as a canonical :class:`GrpcRequest`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: request.json must be a top-level object"
        raise TypeError(msg)
    calls_raw = data.get("calls")
    if not isinstance(calls_raw, list) or not calls_raw:
        msg = f"{path}: 'calls' must be a non-empty list"
        raise ValueError(msg)
    calls: list[GrpcCall] = []
    for idx, call in enumerate(calls_raw):
        if not isinstance(call, dict):
            msg = f"{path}: calls[{idx}] must be an object"
            raise TypeError(msg)
        calls.append(_call_from_dict(call, source=f"{path}:calls[{idx}]"))
    return GrpcRequest(calls=tuple(calls))


def _call_from_dict(data: dict[str, Any], *, source: str) -> GrpcCall:
    """Build a :class:`GrpcCall` from a parsed JSON object."""
    method = data.get("method")
    if not isinstance(method, str) or not method:
        msg = f"{source}: 'method' must be a non-empty string"
        raise ValueError(msg)
    if method not in DEFAULT_METHOD_KIND:
        msg = f"{source}: unknown method {method!r}; known: {sorted(DEFAULT_METHOD_KIND)!r}"
        raise ValueError(msg)
    kind = data.get("kind", DEFAULT_METHOD_KIND[method])
    if kind not in GRPC_METHOD_KINDS:
        msg = f"{source}: 'kind' must be one of {sorted(GRPC_METHOD_KINDS)!r}; got {kind!r}"
        raise ValueError(msg)
    request_obj = data.get("request")
    requests_obj = data.get("requests")
    if kind == "bidi_stream":
        if not isinstance(requests_obj, list) or not requests_obj:
            msg = f"{source}: bidi_stream call must carry a non-empty 'requests' list"
            raise ValueError(msg)
        for ridx, item in enumerate(requests_obj):
            if not isinstance(item, dict):
                msg = f"{source}: requests[{ridx}] must be an object"
                raise TypeError(msg)
        requests_tuple = tuple(requests_obj)
        request_single = None
    else:
        if not isinstance(request_obj, dict):
            msg = f"{source}: {kind!r} call must carry a 'request' object"
            raise ValueError(msg)
        request_single = request_obj
        requests_tuple = ()
    capture = _coerce_capture(data.get("capture"), source=source)
    return GrpcCall(
        method=method,
        kind=kind,
        request=request_single,
        requests=requests_tuple,
        capture=capture,
    )


def _coerce_capture(raw: object, *, source: str) -> tuple[tuple[str, str], ...]:
    """Coerce a ``capture`` JSON object to an ordered tuple."""
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        msg = f"{source}: 'capture' must be a string→string object"
        raise TypeError(msg)
    out: list[tuple[str, str]] = []
    for name, path_value in raw.items():
        if not isinstance(name, str) or not name.isidentifier() or not name.isupper():
            msg = f"{source}: capture key must be an UPPER_SNAKE identifier; got {name!r}"
            raise ValueError(msg)
        if not isinstance(path_value, str) or not path_value:
            msg = f"{source}: capture[{name!r}] must be a non-empty dotted path"
            raise ValueError(msg)
        out.append((name, path_value))
    return tuple(out)


def _load_expected_response(path: Path) -> GrpcExpectedResponse:
    """Read ``expected_response.json`` as the recorded baseline."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: expected_response.json must be a top-level object"
        raise TypeError(msg)
    calls_raw = data.get("calls")
    if not isinstance(calls_raw, list) or not calls_raw:
        msg = f"{path}: 'calls' must be a non-empty list"
        raise ValueError(msg)
    out: list[GrpcExpectedCall] = []
    for idx, entry in enumerate(calls_raw):
        if not isinstance(entry, dict):
            msg = f"{path}: calls[{idx}] must be an object"
            raise TypeError(msg)
        out.append(_expected_call_from_dict(entry, source=f"{path}:calls[{idx}]"))
    return GrpcExpectedResponse(calls=tuple(out))


def _expected_call_from_dict(data: dict[str, Any], *, source: str) -> GrpcExpectedCall:
    """Build a :class:`GrpcExpectedCall` from a parsed JSON object."""
    method = data.get("method")
    status = data.get("status")
    if not isinstance(method, str) or not method:
        msg = f"{source}: 'method' must be a non-empty string"
        raise ValueError(msg)
    if not isinstance(status, str) or not status:
        msg = f"{source}: 'status' must be a non-empty string"
        raise ValueError(msg)
    response = data.get("response")
    if response is not None and not isinstance(response, dict):
        msg = f"{source}: 'response' must be an object when present"
        raise TypeError(msg)
    responses_raw = data.get("responses")
    responses: tuple[dict[str, Any], ...] | None = None
    if responses_raw is not None:
        if not isinstance(responses_raw, list):
            msg = f"{source}: 'responses' must be a list when present"
            raise TypeError(msg)
        for ridx, item in enumerate(responses_raw):
            if not isinstance(item, dict):
                msg = f"{source}: responses[{ridx}] must be an object"
                raise TypeError(msg)
        responses = tuple(responses_raw)
    error_message = data.get("error_message")
    if error_message is not None and not isinstance(error_message, str):
        msg = f"{source}: 'error_message' must be a string when present"
        raise TypeError(msg)
    return GrpcExpectedCall(
        method=method,
        status=status,
        response=response,
        responses=responses,
        error_message=error_message,
    )


def expand_placeholders(text: str, mapping: dict[str, str]) -> str:
    """Expand every ``${TOKEN}`` placeholder in ``text`` using ``mapping``.

    Mirrors :func:`tests.conformance._http_corpus.expand_placeholders` —
    duplicated here so the gRPC corpus does not depend on the HTTP
    corpus's placeholder pattern (the two could diverge later).
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in mapping:
            msg = f"Unknown placeholder: ${{{name}}} (known: {sorted(mapping.keys())!r})"
            raise ValueError(msg)
        return mapping[name]

    return _CAPTURE_PLACEHOLDER_PATTERN.sub(_replace, text)


def expand_placeholders_in_json(value: object, mapping: dict[str, str]) -> object:
    """Recursively substitute placeholders in a JSON-shaped value."""
    if isinstance(value, str):
        return expand_placeholders(value, mapping)
    if isinstance(value, list):
        return [expand_placeholders_in_json(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: expand_placeholders_in_json(val, mapping) for key, val in value.items()}
    return value


def resolve_dotted_path(value: object, dotted: str) -> object:
    """Walk a dotted path through a JSON-shaped value.

    Supports dict-key descent (``streams.0.name``) and list indexing
    by positive integer. Raises :class:`KeyError` when a key is
    missing or :class:`IndexError` when an index is out of bounds.
    """
    cursor: object = value
    for segment in dotted.split("."):
        if isinstance(cursor, dict):
            if segment not in cursor:
                msg = f"capture path: key {segment!r} not in {sorted(cursor.keys())!r}"
                raise KeyError(msg)
            cursor = cursor[segment]
        elif isinstance(cursor, list):
            try:
                idx = int(segment)
            except ValueError as exc:
                msg = f"capture path: expected integer index, got {segment!r}"
                raise KeyError(msg) from exc
            if idx < 0 or idx >= len(cursor):
                msg = f"capture path: index {idx} out of bounds for list of length {len(cursor)}"
                raise IndexError(msg)
            cursor = cursor[idx]
        else:
            msg = (
                f"capture path: cannot descend into {type(cursor).__name__} at segment {segment!r}"
            )
            raise KeyError(msg)
    return cursor


def proto_class_for(method: str, *, role: str) -> type:
    """Return the proto-plus class for ``role`` (``request``/``response``).

    Used by both the runner and the recorder to deserialise / build
    the messages for each call. Lifted here so the mapping lives next
    to :data:`DEFAULT_METHOD_KIND` and changes to the gRPC surface
    touch one place.
    """
    from google.cloud.bigquery_storage_v1 import types

    if role not in ("request", "response"):
        msg = f"role must be 'request' or 'response'; got {role!r}"
        raise ValueError(msg)
    mapping: dict[tuple[str, str], type] = {
        ("BigQueryRead.CreateReadSession", "request"): types.CreateReadSessionRequest,
        ("BigQueryRead.CreateReadSession", "response"): types.ReadSession,
        ("BigQueryRead.ReadRows", "request"): types.ReadRowsRequest,
        ("BigQueryRead.ReadRows", "response"): types.ReadRowsResponse,
        ("BigQueryRead.SplitReadStream", "request"): types.SplitReadStreamRequest,
        ("BigQueryRead.SplitReadStream", "response"): types.SplitReadStreamResponse,
        ("BigQueryWrite.CreateWriteStream", "request"): types.CreateWriteStreamRequest,
        ("BigQueryWrite.CreateWriteStream", "response"): types.WriteStream,
        ("BigQueryWrite.AppendRows", "request"): types.AppendRowsRequest,
        ("BigQueryWrite.AppendRows", "response"): types.AppendRowsResponse,
        ("BigQueryWrite.GetWriteStream", "request"): types.GetWriteStreamRequest,
        ("BigQueryWrite.GetWriteStream", "response"): types.WriteStream,
        ("BigQueryWrite.FinalizeWriteStream", "request"): types.FinalizeWriteStreamRequest,
        ("BigQueryWrite.FinalizeWriteStream", "response"): types.FinalizeWriteStreamResponse,
        (
            "BigQueryWrite.BatchCommitWriteStreams",
            "request",
        ): types.BatchCommitWriteStreamsRequest,
        (
            "BigQueryWrite.BatchCommitWriteStreams",
            "response",
        ): types.BatchCommitWriteStreamsResponse,
        ("BigQueryWrite.FlushRows", "request"): types.FlushRowsRequest,
        ("BigQueryWrite.FlushRows", "response"): types.FlushRowsResponse,
    }
    try:
        return mapping[method, role]
    except KeyError as exc:
        msg = f"No {role} proto class for method {method!r}"
        raise ValueError(msg) from exc


def proto_to_dict(message: Any) -> dict[str, Any]:
    """Serialise a proto-plus message to a JSON-compatible dict.

    Uses proto-plus's ``to_json`` round-trip so bytes fields land as
    base64-encoded strings (matching the comparator's expectations).
    The output preserves snake_case field names and renders enums as
    their string names. Default-valued fields are omitted (matching
    real BigQuery's wire shape — proto3 omits singular default values
    on the wire).
    """
    cls = type(message)
    # Note: proto-plus deprecated ``including_default_value_fields`` in
    # favour of ``always_print_fields_with_no_presence`` (protobuf 5.x).
    # Omitting both keeps the default ("omit fields without presence
    # whose value matches the default") which matches real BQ's shape.
    json_str = cls.to_json(
        message,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
    return json.loads(json_str)  # type: ignore[no-any-return]


def proto_from_dict(method: str, role: str, data: dict[str, Any]) -> Any:
    """Build a proto-plus message from a JSON-compatible dict."""
    cls = proto_class_for(method, role=role)
    return cls.from_json(json.dumps(data), ignore_unknown_fields=False)


def serialize_request(method: str, payload: dict[str, Any]) -> bytes:
    """Serialise a request payload dict to wire bytes for ``method``."""
    message = proto_from_dict(method, "request", payload)
    cls = proto_class_for(method, role="request")
    return cls.serialize(message)  # type: ignore[no-any-return]


def deserialize_response(method: str, payload: bytes) -> dict[str, Any]:
    """Deserialise a wire-bytes response for ``method`` back into a JSON dict."""
    cls = proto_class_for(method, role="response")
    message = cls.deserialize(payload)
    return proto_to_dict(message)


__all__ = [
    "DEFAULT_METHOD_KIND",
    "GRPC_CORPUS_DIR",
    "GRPC_METHOD_KINDS",
    "GRPC_PHASE_SUBDIRS",
    "GRPC_SERVICE_PATHS",
    "WILDCARD",
    "GrpcCall",
    "GrpcExpectedCall",
    "GrpcExpectedResponse",
    "GrpcFixture",
    "GrpcRequest",
    "deserialize_response",
    "discover_grpc_fixtures",
    "expand_placeholders",
    "expand_placeholders_in_json",
    "proto_class_for",
    "proto_from_dict",
    "proto_to_dict",
    "resolve_dotted_path",
    "serialize_request",
]
