"""HTTP-shape conformance corpus — discovery + per-fixture bundles.

The HTTP corpus lives at ``tests/conformance/http_corpus/`` and is a
sibling of ``sql_corpus/``. Each fixture exercises a non-SQL REST
response shape — pagination, job lifecycle (``jobs.get`` /
``jobs.list`` / ``jobs.cancel`` / ``jobs.delete``), and ``dryRun``
preview semantics — that the row+schema diff in ``sql_corpus/``
cannot express. Locked by P2.f.

Fixture layout::

    tests/conformance/http_corpus/<phase>/<name>/
        setup.sql               # optional — table seed run before any REST call
        setup_requests.json     # optional — ordered REST calls run before the canonical request
        request.json            # the canonical REST call diffed against the recorded baseline
        expected_response.json  # recorded baseline (status + body, optionally a header subset)

``setup_requests.json`` is an ordered list of REST operations. Each
entry carries ``method`` / ``path`` / ``body`` (the same shape as
``setup_rest.json`` in ``sql_corpus/``) plus an optional ``capture``
map. Captured values are added to the placeholder context so the
canonical ``request.json`` can substitute ``${JOB_ID}`` / ``${PAGE_TOKEN}``
/ etc. picked up from the previous step's response body. The capture
syntax is a dotted JSON path: ``jobReference.jobId``,
``jobs.0.jobReference.jobId``. List indexing uses positive integers.

A setup entry may also carry ``await_job``: the name of a captured
variable holding a BigQuery job id. At record time the recorder blocks
until that job finishes before issuing the next request, so a fixture
that uploads an asynchronous load job can then GET the resulting table
once its inferred schema exists. The runner ignores ``await_job`` — the
emulator executes loads synchronously, so the table is already present.

``request.json`` and ``expected_response.json`` are the canonical pair
the comparator diffs:

- ``request.json`` carries ``method``, ``path``, an optional
  ``body``, and an optional ``headers`` dict.
- ``expected_response.json`` carries ``http_status``, ``body``, and
  (optionally) ``headers``. A recorded ``body`` is a partial schema:
  fields the recorded value lists must be present on the emulator
  response, but extra emulator-side fields are tolerated. Server-
  generated opaque values (job ids, etags, opaque self-links) are
  recorded as the wildcard sentinel ``"<*>"``; the comparator only
  checks that the key is present, not its value.

The placeholder set is shared with ``sql_corpus/``
(``${PROJECT}`` / ``${DATASET}`` / ``${DATASET_ID}`` / ``${PRINCIPAL}`` /
``${GROUP}`` / ``${OTHER_PRINCIPAL}``) and extended at runtime with
the union of any ``capture``d setup-response values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

# Repository-relative path: ``tests/conformance/http_corpus``. Mirrors
# :data:`tests.conformance._corpus.CORPUS_DIR` so the recorder + runner
# discover both kinds the same way.
HTTP_CORPUS_DIR = Path(__file__).parent / "http_corpus"

# HTTP phase sub-directories. Order is stable so parametrised tests
# have predictable IDs.
HTTP_PHASE_SUBDIRS: tuple[str, ...] = ("jobs",)

# Sentinel for "any value is acceptable at this key". The recorder
# writes this where the recorded BigQuery response carried a server-
# generated opaque value (job id, etag, opaque self-link, timestamps).
# The comparator treats a key with this value as "must be present;
# value not checked".
WILDCARD = "<*>"

# Captured-variable placeholder pattern. Disjoint from the existing
# ``${UPPER_TOKEN}`` placeholders so a typo fails loudly at runtime
# rather than silently leaking through.
_CAPTURE_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


@dataclass(slots=True, frozen=True)
class HttpRequest:
    """One HTTP request — setup or canonical.

    ``body`` carries the JSON body for regular REST calls. For upload-
    host fixtures (G2) whose body is binary (multipart envelope, file
    bytes), ``body_bin`` names a sibling file under the fixture
    directory and the runner reads it as raw bytes — bypassing JSON
    serialisation entirely. Mutually exclusive with ``body``.
    """

    method: str
    path: str
    body: object | None = None
    body_bin: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    capture: tuple[tuple[str, str], ...] = ()
    await_job: str | None = None


@dataclass(slots=True, frozen=True)
class HttpExpectedResponse:
    """Recorded baseline response for the canonical request."""

    http_status: int
    body: object
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True, frozen=True)
class HttpFixture:
    """One HTTP conformance fixture on disk."""

    phase: str
    name: str
    path: Path
    setup_sql: str | None
    setup_requests: tuple[HttpRequest, ...]
    request: HttpRequest
    expected_path: Path
    expected: HttpExpectedResponse | None = field(default=None)

    @property
    def id(self) -> str:
        """The parametrize-friendly identifier, e.g. ``jobs/page_first_page_only``."""
        return f"{self.phase}/{self.name}"

    @property
    def needs_dataset(self) -> bool:
        """True when the fixture has a ``setup.sql`` requiring a temp dataset.

        Setup REST calls alone do not imply a dataset — many of them
        create their own datasets via ``POST /datasets``. Only
        ``setup.sql`` requires the runner / recorder to pre-create one.
        """
        return self.setup_sql is not None


def discover_http_fixtures(
    corpus_dir: Path | None = None,
    *,
    include_unrecorded: bool = False,
) -> list[HttpFixture]:
    """Walk the HTTP corpus and return every fixture directory.

    A fixture directory is recognised by the presence of
    ``request.json``. By default fixtures without an
    ``expected_response.json`` are excluded so the runner only attempts
    to compare against recorded baselines; the recorder passes
    ``include_unrecorded=True`` to surface them.
    """
    root = corpus_dir or HTTP_CORPUS_DIR
    fixtures: list[HttpFixture] = []
    for phase in HTTP_PHASE_SUBDIRS:
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
            expected: HttpExpectedResponse | None = None
            if expected_path.is_file():
                expected = _load_expected_response(expected_path)
            fixtures.append(
                HttpFixture(
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

    The expected shape is a top-level list. Each entry must carry
    ``method`` + ``path``; ``body``, ``headers``, and ``capture`` are
    optional. Validation here is intentionally minimal — the runner
    and recorder re-validate when issuing the call.
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


def _load_request(path: Path) -> HttpRequest:
    """Read ``request.json`` as the canonical ``HttpRequest``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: request.json must be a top-level object"
        raise TypeError(msg)
    return _request_from_dict(data, source=str(path))


def _request_from_dict(data: dict[str, Any], *, source: str) -> HttpRequest:
    """Build an ``HttpRequest`` from a parsed JSON dict."""
    method = data.get("method")
    path_value = data.get("path")
    if not isinstance(method, str) or not method:
        msg = f"{source}: 'method' must be a non-empty string"
        raise ValueError(msg)
    if not isinstance(path_value, str) or not path_value:
        msg = f"{source}: 'path' must be a non-empty string"
        raise ValueError(msg)
    body = data.get("body")
    body_bin = data.get("body_bin")
    if body is not None and body_bin is not None:
        msg = f"{source}: 'body' and 'body_bin' are mutually exclusive"
        raise ValueError(msg)
    if body_bin is not None and not isinstance(body_bin, str):
        msg = f"{source}: 'body_bin' must be a string filename relative to the fixture dir"
        raise TypeError(msg)
    headers = _coerce_headers(data.get("headers"), source=source)
    capture = _coerce_capture(data.get("capture"), source=source)
    await_job = data.get("await_job")
    if await_job is not None and (not isinstance(await_job, str) or not await_job):
        msg = f"{source}: 'await_job' must be a non-empty captured-variable name"
        raise ValueError(msg)
    return HttpRequest(
        method=method.upper(),
        path=path_value,
        body=body,
        body_bin=body_bin,
        headers=headers,
        capture=capture,
        await_job=await_job,
    )


def _coerce_headers(raw: object, *, source: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        msg = f"{source}: 'headers' must be a string→string object"
        raise TypeError(msg)
    out: list[tuple[str, str]] = []
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            msg = f"{source}: header entries must be string→string"
            raise TypeError(msg)
        out.append((name, value))
    return tuple(out)


def _coerce_capture(raw: object, *, source: str) -> tuple[tuple[str, str], ...]:
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
            msg = f"{source}: capture[{name!r}] must be a non-empty dotted JSON path"
            raise ValueError(msg)
        out.append((name, path_value))
    return tuple(out)


def _load_expected_response(path: Path) -> HttpExpectedResponse:
    """Read ``expected_response.json`` as the recorded baseline."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: expected_response.json must be a top-level object"
        raise TypeError(msg)
    status = data.get("http_status")
    if not isinstance(status, int):
        msg = f"{path}: 'http_status' must be an integer"
        raise TypeError(msg)
    body = data.get("body")
    if body is None:
        # An empty 204 response carries no body. We model that as the
        # explicit empty-string sentinel so the comparator can diff
        # against the actual empty body symmetrically.
        body = ""
    headers = _coerce_headers(data.get("headers"), source=str(path))
    return HttpExpectedResponse(http_status=status, body=body, headers=headers)


def resolve_dotted_path(value: object, dotted: str) -> object:
    """Walk a dotted path through a JSON-shaped value.

    Supports dict-key descent (``jobReference.jobId``) and list
    indexing by positive integer (``jobs.0.jobReference.jobId``). Raises
    :class:`KeyError` when a key is missing or :class:`IndexError` when
    an index is out of bounds — both bubble up to the runner so a
    misconfigured capture fails loudly rather than silently leaking
    ``None`` into the canonical request's substitutions.
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


def expand_placeholders(text: str, mapping: dict[str, str]) -> str:
    """Expand every ``${TOKEN}`` placeholder in ``text`` using ``mapping``.

    Any token not present in ``mapping`` raises so a typo fails loudly
    at runtime rather than silently leaking through (matches the
    contract of :func:`tests.conformance._corpus.substitute_placeholders`).
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in mapping:
            msg = f"Unknown placeholder: ${{{name}}} (known: {sorted(mapping.keys())!r})"
            raise ValueError(msg)
        return mapping[name]

    return _CAPTURE_PLACEHOLDER_PATTERN.sub(_replace, text)


def expand_placeholders_in_json(value: object, mapping: dict[str, str]) -> object:
    """Recursively substitute placeholders in a JSON-shaped value.

    Strings are passed through :func:`expand_placeholders`; lists and
    dicts are walked element-wise. Numbers, booleans, and ``None`` are
    returned unchanged.
    """
    if isinstance(value, str):
        return expand_placeholders(value, mapping)
    if isinstance(value, list):
        return [expand_placeholders_in_json(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: expand_placeholders_in_json(val, mapping) for key, val in value.items()}
    return value


__all__ = [
    "HTTP_CORPUS_DIR",
    "HTTP_PHASE_SUBDIRS",
    "WILDCARD",
    "HttpExpectedResponse",
    "HttpFixture",
    "HttpRequest",
    "discover_http_fixtures",
    "expand_placeholders",
    "expand_placeholders_in_json",
    "resolve_dotted_path",
]
