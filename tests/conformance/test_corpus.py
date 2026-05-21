"""Parametrised conformance runner.

Every fixture under ``sql_corpus/`` becomes one parametrised test. The
fixture's ``query.sql`` is executed against an in-process emulator and
the result is diffed against the recorded ``expected.json`` baseline
with :func:`tests.conformance._comparison.compare_results`.

The pass-rate gate is enforced via ``@pytest.mark.xfail(strict=True)``
applied to fixtures listed in :data:`KNOWN_DIVERGENCES` — divergences
must be explicit, rooted in an ADR, and accompanied by a rationale.

Error-shape parity (ADR 0022 §3, P3.a). A fixture whose recorded
``expected.json`` carries an ``error`` envelope is expected to fail
against the emulator. The runner catches the ``GoogleAPIError``, runs
:func:`tests.conformance._comparison.compare_error`, and diffs
``reason`` / ``location`` / ``http_status`` (exact) plus a regex
match against ``message_pattern``. A success fixture that the
emulator now errors on (or an error fixture the emulator now
succeeds on) fails with a clear message.

Caller-identity / REST setup (ADR 0018, P2.d). A fixture may carry
``headers.json`` (per-query HTTP headers like ``X-Bqemu-Caller``) and
``setup_rest.json`` (an ordered list of REST API operations applied
before ``query.sql``). Both files are optional and additive; they
let Phase 8 RAP fixtures express the caller-bound enforcement the
SQL surface alone cannot.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
import uuid

import httpx
import pytest

from tests.conformance._comparison import (
    compare_error,
    compare_results,
    extract_actual_error,
)
from tests.conformance._corpus import (
    DEFAULT_RUNNER_GROUP,
    DEFAULT_RUNNER_OTHER_PRINCIPAL,
    DEFAULT_RUNNER_PRINCIPAL,
    Fixture,
    PlaceholderContext,
    discover_fixtures,
    split_statements,
    substitute_in_json,
    substitute_placeholders,
)
from tests.conformance._job_config import build_job_config
from tests.conformance._parameters import build_query_parameters
from tests.conformance.divergences import KNOWN_DIVERGENCES

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.testing.fixtures import EmulatorEndpoint

#: HTTP success ceiling. Any status code at or above this value is
#: treated as a setup failure by the REST helper.
_HTTP_SUCCESS_CEILING = 300

ALL_FIXTURES = discover_fixtures()


def _fixture_id(fixture: Fixture) -> str:
    return fixture.id


def _parametrize_fixtures() -> list[pytest.param]:
    """Wrap every fixture in a ``pytest.param`` annotated with any xfail.

    A fixture in :data:`KNOWN_DIVERGENCES` carries an xfail marker
    with ``strict=True``: an unexpected pass fails the suite just as
    an unexpected fail does. The xfail reason includes the ADR or
    ``out-of-scope.md`` anchor.
    """
    params: list[pytest.param] = []
    for fixture in ALL_FIXTURES:
        marks: list[pytest.MarkDecorator] = []
        divergence = KNOWN_DIVERGENCES.get(fixture.id)
        if divergence is not None:
            marks.append(pytest.mark.xfail(strict=True, reason=divergence))
        params.append(pytest.param(fixture, id=fixture.id, marks=marks))
    return params


@pytest.mark.parametrize("fixture", _parametrize_fixtures())
def test_conformance_fixture(
    fixture: Fixture,
    bqemu_endpoint: EmulatorEndpoint,
) -> None:
    """Replay ``fixture`` against the emulator and diff vs the baseline.

    For fixtures with a ``setup.sql`` or ``setup_rest.json``, a unique
    dataset is created and dropped per test. Literal-only fixtures
    skip dataset creation entirely. The recorded expected payload's
    ``schema`` + ``rows`` drives the diff; the recorded BigQuery
    metadata is read for the diagnostic message only.

    Error-shape fixtures (ADR 0022 §3 ``Error parity``, P3.a) carry
    an ``error`` envelope on ``expected.json``. The runner expects the
    emulator's query to raise ``GoogleAPIError`` and routes the
    extracted error shape through :func:`compare_error`. A success
    fixture that errors (or an error fixture that succeeds) fails
    with a clear cross-kind message before any diff runs.
    """
    expected = json.loads(fixture.expected_path.read_text(encoding="utf-8"))
    expected_error = expected.get("error")

    bq_job = expected.get("bigquery", {}).get("job_id", "<no job_id>")
    actual_rows, actual_schema, actual_error, actual_job_metadata = _execute_fixture(
        fixture, bqemu_endpoint
    )

    if expected_error is not None:
        if actual_error is None:
            pytest.fail(
                f"Conformance kind mismatch for {fixture.id} (BQ job {bq_job}): "
                f"expected an error envelope but emulator succeeded with "
                f"{len(actual_rows)} row(s) and schema={actual_schema!r}"
            )
        report = compare_error(expected_error, actual_error)
    else:
        if actual_error is not None:
            pytest.fail(
                f"Conformance kind mismatch for {fixture.id} (BQ job {bq_job}): "
                f"expected rows + schema but emulator raised: "
                f"{actual_error.get('message', '<no message>')}"
            )
        report = compare_results(
            expected,
            actual_rows,
            actual_schema,
            actual_job_metadata=actual_job_metadata,
        )

    if not report.ok:
        diagnostic = "\n".join(report.diffs)
        pytest.fail(f"Conformance diff for {fixture.id} (BQ job {bq_job}):\n{diagnostic}")


def _execute_fixture(
    fixture: Fixture, endpoint: EmulatorEndpoint
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, Any] | None,
    dict[str, Any],
]:
    """Execute one fixture against the emulator and capture success or error.

    Returns a 4-tuple ``(rows, schema, error, job_metadata)``:

    - On success: ``rows`` + ``schema`` are populated, ``error`` is
      ``None``, and ``job_metadata`` carries the response-equivalence
      fields the emulator's QueryJob surfaces (cache_hit,
      statement_type, num_dml_affected_rows, ddl_operation_performed).
      The dict is always returned even when empty so the runner can
      pass it through to :func:`compare_results` unconditionally.
    - On a ``GoogleAPIError`` raised by the BigQuery client during
      the ``query.sql`` execution (NOT during setup): the rows /
      schema / job_metadata are empty and ``error`` is the
      normalised shape returned by :func:`extract_actual_error`.
    - Any other exception (setup failure, network drop, client
      construction error) propagates so the test fails with a stack
      trace.

    When ``fixture.headers`` is non-empty the BigQuery client is
    constructed with a custom ``AuthorizedSession`` carrying those
    headers, so the canonical ``query.sql`` request includes them.
    The headers are NOT applied during setup — setup runs as the
    anonymous/default identity, mirroring how Phase 8 integration
    tests already operate.
    """
    from google.api_core.client_options import ClientOptions
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    project = endpoint.project_id

    setup_client = bigquery.Client(
        project=project,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=endpoint.rest_url),
    )

    dataset_fqdn: str | None = None
    if fixture.needs_dataset:
        dataset_name = f"bqemu_conformance_{uuid.uuid4().hex[:12]}"
        dataset_fqdn = f"{project}.{dataset_name}"
        setup_client.create_dataset(bigquery.Dataset(dataset_fqdn))

    ctx = PlaceholderContext(
        dataset=dataset_fqdn or f"{project}.bqemu_unused",
        principal=DEFAULT_RUNNER_PRINCIPAL,
        group=DEFAULT_RUNNER_GROUP,
        other_principal=DEFAULT_RUNNER_OTHER_PRINCIPAL,
    )

    actual_rows: list[dict[str, object]] = []
    actual_schema: list[dict[str, object]] = []
    actual_error: dict[str, Any] | None = None
    actual_job_metadata: dict[str, Any] = {}
    rest_created_datasets: list[tuple[str, str]] = []
    try:
        if fixture.setup_sql is not None:
            assert dataset_fqdn is not None  # narrowing for mypy
            for stmt in split_statements(substitute_placeholders(fixture.setup_sql, ctx)):
                setup_client.query(stmt).result()

        if fixture.setup_rest:
            rest_created_datasets = _apply_setup_rest(endpoint.rest_url, fixture.setup_rest, ctx)

        query_sql = substitute_placeholders(fixture.query_sql, ctx)
        request_headers = _resolve_headers(fixture.headers, ctx)
        query_client = _query_client(
            project=project,
            rest_url=endpoint.rest_url,
            headers=request_headers,
        )
        job_config = _build_job_config(fixture, ctx)
        try:
            query_job = (
                query_client.query(query_sql, job_config=job_config)
                if job_config is not None
                else query_client.query(query_sql)
            )
            result = query_job.result()
        except GoogleAPIError as exc:
            actual_error = extract_actual_error(exc)
        else:
            actual_rows = _result_to_rows(result)
            actual_schema = _result_to_schema(result)
            # P7.a — capture the emulator's job-metadata response so the
            # comparator can diff against the recorded baseline's
            # optional ``job_metadata`` block.
            actual_job_metadata = _extract_actual_job_metadata(query_job)
    finally:
        for proj_id, ds_id in reversed(rest_created_datasets):
            setup_client.delete_dataset(
                f"{proj_id}.{ds_id}", delete_contents=True, not_found_ok=True
            )
        if dataset_fqdn is not None:
            setup_client.delete_dataset(dataset_fqdn, delete_contents=True, not_found_ok=True)
    return actual_rows, actual_schema, actual_error, actual_job_metadata


def _extract_actual_job_metadata(query_job: Any) -> dict[str, Any]:
    """Capture the emulator's job-metadata response for the runner's diff.

    Mirrors the recorder's
    :func:`scripts.record_conformance_fixtures._extract_job_metadata`
    so the fixture's recorded baseline and the emulator's runtime
    output use the same key set. Missing fields surface as absent
    keys (not ``None`` values) so the comparator can report
    ``actual=<absent>`` cleanly.

    Note: ``cache_hit`` defaults to ``False`` on the BigQuery
    Python-client `QueryJob` even when the underlying REST response
    omits it; we propagate that as-is. The emulator is expected to
    return ``cache_hit=False`` for every fixture because it has no
    query cache; the recorded baseline lets a fixture document this
    explicitly.
    """
    metadata: dict[str, Any] = {}
    cache_hit = getattr(query_job, "cache_hit", None)
    if cache_hit is not None:
        metadata["cache_hit"] = bool(cache_hit)
    statement_type = getattr(query_job, "statement_type", None)
    if statement_type:
        metadata["statement_type"] = str(statement_type)
    num_dml = getattr(query_job, "num_dml_affected_rows", None)
    if num_dml is not None:
        metadata["num_dml_affected_rows"] = int(num_dml)
    ddl_op = getattr(query_job, "ddl_operation_performed", None)
    if ddl_op:
        metadata["ddl_operation_performed"] = str(ddl_op)
    return metadata


def _query_client(*, project: str, rest_url: str, headers: dict[str, str]) -> Any:
    """Build a BigQuery client with optional per-request headers.

    When ``headers`` is empty we return a plain client — zero behavioural
    change for the overwhelming majority of fixtures. Otherwise we
    construct an :class:`AuthorizedSession` carrying the fixture's
    headers and pass it as the client's ``_http`` transport so every
    HTTP request the client issues during this fixture (the synchronous
    /queries call and any polling) sees them.
    """
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import bigquery

    if not headers:
        return bigquery.Client(
            project=project,
            credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
            client_options=ClientOptions(api_endpoint=rest_url),
        )

    session = AuthorizedSession(AnonymousCredentials())  # type: ignore[no-untyped-call]
    for name, value in headers.items():
        session.headers[name] = value
    return bigquery.Client(
        project=project,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=rest_url),
        _http=session,
    )


_DATASET_CREATE_PATH = re.compile(r"^/bigquery/v2/projects/(?P<project>[^/]+)/datasets/?$")


def _apply_setup_rest(
    rest_url: str,
    operations: tuple[dict[str, object], ...],
    ctx: PlaceholderContext,
) -> list[tuple[str, str]]:
    """Apply ``setup_rest.json`` operations against the emulator base URL.

    Operations are issued in order; the first non-2xx response raises
    so a fixture's REST setup fails loudly rather than corrupting the
    catalog. The emulator does not enforce auth, so we issue plain
    ``httpx`` calls — no ``AuthorizedSession`` is needed.

    Returns a list of ``(project, dataset_id)`` pairs created via
    ``POST /bigquery/v2/projects/<p>/datasets``. The caller is
    responsible for deleting these on teardown so secondary datasets
    used by authorized-view fixtures don't leak.
    """
    created: list[tuple[str, str]] = []
    with httpx.Client(base_url=rest_url, timeout=30.0) as http:
        for raw in operations:
            method = str(raw["method"]).upper()
            path = substitute_placeholders(str(raw["path"]), ctx)
            body_raw = raw.get("body")
            body = substitute_in_json(body_raw, ctx) if body_raw is not None else None
            response = http.request(method, path, json=body)
            if response.status_code >= _HTTP_SUCCESS_CEILING:
                msg = (
                    f"setup_rest.json {method} {path} returned "
                    f"{response.status_code}: {response.text}"
                )
                raise RuntimeError(msg)
            tracked = _track_dataset_creation(method, path, body)
            if tracked is not None:
                created.append(tracked)
    return created


def _track_dataset_creation(
    method: str,
    path: str,
    body: object,
) -> tuple[str, str] | None:
    """Detect ``POST /projects/<p>/datasets`` and return the (project, id) pair.

    Returns ``None`` for any other request. Used by both the runner
    and the recorder to track secondary datasets for teardown.
    """
    if method != "POST":
        return None
    match = _DATASET_CREATE_PATH.match(path)
    if match is None:
        return None
    project = match.group("project")
    if not isinstance(body, dict):
        return None
    ref = body.get("datasetReference")
    if not isinstance(ref, dict):
        return None
    dataset_id = ref.get("datasetId")
    if not isinstance(dataset_id, str) or not dataset_id:
        return None
    return project, dataset_id


def _resolve_headers(
    headers: tuple[tuple[str, str], ...],
    ctx: PlaceholderContext,
) -> dict[str, str]:
    """Substitute placeholders in fixture headers and return as a dict."""
    return {name: substitute_placeholders(value, ctx) for name, value in headers}


def _build_job_config(fixture: Fixture, ctx: PlaceholderContext) -> Any | None:
    """Construct a ``QueryJobConfig`` from the fixture's parameters + job_config.

    Returns ``None`` for fixtures without either ``parameters.json``
    or ``job_config.json`` so the runner takes the plain
    ``client.query(sql)`` path unchanged.

    Composition rules (P2.e + P7.a):

    - When only ``parameters.json`` is present, a fresh
      ``QueryJobConfig`` is built with the parameter list and
      returned (P2.e behaviour).
    - When only ``job_config.json`` is present, the configured
      ``QueryJobConfig`` is built via
      :func:`tests.conformance._job_config.build_job_config` (P7.a
      behaviour).
    - When BOTH are present, the ``job_config.json`` is the base and
      the parameter list is set on its ``query_parameters``
      attribute. This lets a fixture exercise (e.g.) a
      ``priority=BATCH`` job that also binds query parameters.

    Both payloads are round-tripped through
    :func:`substitute_in_json` so ``${…}`` placeholders inside
    values are expanded before submission.
    """
    if fixture.parameters is None and fixture.job_config is None:
        return None
    from google.cloud import bigquery

    if fixture.job_config is not None:
        expanded_config = substitute_in_json(fixture.job_config, ctx)
        if not isinstance(expanded_config, dict):  # pragma: no cover - guarded by _load_job_config
            msg = f"{fixture.id}: job_config payload must be a dict after substitution"
            raise TypeError(msg)
        config = build_job_config(expanded_config)
    else:
        config = bigquery.QueryJobConfig()

    if fixture.parameters is not None:
        expanded_params = substitute_in_json(fixture.parameters, ctx)
        if not isinstance(expanded_params, dict):  # pragma: no cover - guarded by _load_parameters
            msg = f"{fixture.id}: parameters payload must be a dict after substitution"
            raise TypeError(msg)
        params = build_query_parameters(expanded_params)
        config.query_parameters = params

    return config


def _result_to_rows(result: object) -> list[dict[str, object]]:
    """Convert a BigQuery ``QueryResult`` into JSON-friendly rows.

    Mirrors the recorder's row encoder so the runner produces output
    in the same shape as ``expected.json`` — the comparison helper
    can then diff without any further normalisation.
    """
    from tests.conformance._row_encoding import row_to_jsonable

    rows: list[dict[str, object]] = []
    # google-cloud-bigquery's RowIterator is iterable.
    for row in result:  # type: ignore[attr-defined]
        encoded: dict[str, object] = {}
        for field_def, value in zip(result.schema, row.values(), strict=True):  # type: ignore[attr-defined]
            encoded[field_def.name] = row_to_jsonable(value, field_def)
        rows.append(encoded)
    return rows


def _result_to_schema(result: object) -> list[dict[str, object]]:
    """Convert a BigQuery ``QueryResult.schema`` into the recorded shape."""
    from tests.conformance._row_encoding import field_to_jsonable

    return [field_to_jsonable(f) for f in result.schema]  # type: ignore[attr-defined]
