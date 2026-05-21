#!/usr/bin/env python3
"""Record conformance baselines from real BigQuery.

For every fixture under ``tests/conformance/sql_corpus/`` this script:

1. (Optionally) executes ``setup.sql`` against a per-fixture temp
   dataset on the operator-supplied project.
2. (Optionally) applies the fixture's ``setup_rest.json`` operations
   against BigQuery's REST API (P2.d, Phase 8 row-access fixtures).
3. Submits the fixture's ``query.sql``.
4. Writes the resulting rows + schema + BigQuery job metadata to the
   fixture's ``expected.json``, **or** — when BigQuery raises a
   ``GoogleAPIError`` during the query — writes the error envelope
   (``reason`` / ``location`` / ``http_status`` / ``message_pattern``)
   for the conformance error-shape parity tier (ADR 0022 §3, P3.a).

The fixture's recorded baseline is the canonical ground truth for the
conformance tier (ADR 0022 non-negotiable). Hand-editing an
``expected.json`` is forbidden by Phase 11 non-negotiable #8: every
recorded payload must include the BigQuery job id that produced it,
which the runner cross-checks for audit.

Usage::

    python scripts/record_conformance_fixtures.py \\
        --project your-bigquery-project \\
        --location US

Refuses to overwrite an existing ``expected.json`` unless ``--force``
is supplied. ``--filter <substring>`` re-records only fixtures whose
``<phase>/<name>`` id matches the substring.

For Phase 8 RAP fixtures (P2.d), the operator must export
``BQEMU_CONFORMANCE_PRINCIPAL`` and (optionally)
``BQEMU_CONFORMANCE_GROUP`` so the recorded baselines reflect RAP
enforcement under the ADC identity. The principal is the IAM-member
string of the recording account (e.g.,
``user:test-svc@example.com`` or
``serviceAccount:bqemu-recorder@…iam.gserviceaccount.com``); the
group is one of its memberships (e.g.,
``group:bqemu-recorders@example.com``). Both placeholders are
expanded in ``setup_rest.json`` grantees and in any other corpus
file that references them.

Cost guard: every fixture's ``total_bytes_processed`` is checked
against a configurable byte-scan cap (default 1 GiB). A fixture that
would exceed the cap is logged and skipped without writing
``expected.json``; the script exits non-zero so the operator notices.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import uuid

# The recorder lives in scripts/ but reuses the corpus discovery /
# substitution / row-encoding helpers from tests/conformance/. Add the
# repo root to sys.path so ``tests.conformance.*`` is importable when
# invoked as ``python scripts/record_conformance_fixtures.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conformance._corpus import (  # noqa: E402
    CORPUS_DIR,
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
from tests.conformance._job_config import build_job_config  # noqa: E402
from tests.conformance._parameters import build_query_parameters  # noqa: E402
from tests.conformance._row_encoding import field_to_jsonable, row_to_jsonable  # noqa: E402

#: Env var the operator sets to their ADC's IAM-member string so the
#: ``${PRINCIPAL}`` placeholder in P2.d fixtures expands to a value
#: BigQuery's row-access enforcement will actually grant.
PRINCIPAL_ENV = "BQEMU_CONFORMANCE_PRINCIPAL"
GROUP_ENV = "BQEMU_CONFORMANCE_GROUP"
#: Env var the operator sets to a real-but-non-caller IAM-member so
#: the ``${OTHER_PRINCIPAL}`` placeholder in "denied" P2.d fixtures
#: resolves to a principal real BigQuery accepts at policy-creation
#: time. The project's default compute service account
#: (``serviceAccount:<projnum>-compute@developer.gserviceaccount.com``)
#: is a convenient choice — it exists in every project and is never
#: the recorder's ADC identity.
OTHER_PRINCIPAL_ENV = "BQEMU_CONFORMANCE_OTHER_PRINCIPAL"

#: BigQuery's REST API base URL — the recorder's REST setup operations
#: target this; the runner uses the emulator's base URL instead.
BQ_REST_BASE = "https://bigquery.googleapis.com"

#: HTTP success ceiling. Any status code at or above this value is
#: treated as a setup failure by the REST helper.
_HTTP_SUCCESS_CEILING = 300

DEFAULT_BYTE_CAP = 1 * 1024 * 1024 * 1024  # 1 GiB
# Bumped to 2 on 2026-05-17 (P3.a) when the error-envelope shape was
# added. Pre-existing success fixtures stay at version 1; the runner
# branches on the ``error`` field's presence, not on this number.
# Re-recording a v1 success fixture bumps it to v2 cleanly because the
# payload shape is purely additive.
FIXTURE_VERSION = 2

# Duration-class thresholds (informational; not used for comparison).
DURATION_FAST_MS = 1_000
DURATION_MEDIUM_MS = 10_000

logger = logging.getLogger("record_conformance")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from google.cloud import bigquery

    client = bigquery.Client(project=args.project, location=args.location)
    fixtures = discover_fixtures(include_unrecorded=True)
    if args.filter:
        fixtures = [f for f in fixtures if args.filter in f.id]
    if not fixtures:
        logger.error("No fixtures matched filter=%r", args.filter)
        return 1

    principal = os.environ.get(PRINCIPAL_ENV, "").strip()
    group = os.environ.get(GROUP_ENV, "").strip()
    other_principal = os.environ.get(OTHER_PRINCIPAL_ENV, "").strip()
    needs_principal = any(f.setup_rest or f.headers for f in fixtures)
    if needs_principal and not principal:
        logger.error(
            "At least one selected fixture references ${PRINCIPAL} via "
            "setup_rest.json or headers.json. Export %s=<iam-member> "
            "matching the recording account's ADC identity before re-running.",
            PRINCIPAL_ENV,
        )
        return 1

    logger.info(
        "Recording %d fixtures against project=%s (principal=%s group=%s other_principal=%s)",
        len(fixtures),
        args.project,
        principal or "<unset>",
        group or "<unset>",
        other_principal or "<unset>",
    )
    run_id = uuid.uuid4().hex[:12]
    successes = 0
    skipped = 0
    failures: list[tuple[str, str]] = []
    cost_skips: list[str] = []

    for fixture in fixtures:
        if fixture.expected_path.is_file() and not args.force:
            logger.info("[skip] %s (expected.json exists; pass --force to re-record)", fixture.id)
            skipped += 1
            continue
        outcome = _record_one(
            fixture,
            client=client,
            run_id=run_id,
            byte_cap=args.byte_cap,
            dry_run=args.dry_run,
            principal=principal or DEFAULT_RUNNER_PRINCIPAL,
            group=group or DEFAULT_RUNNER_GROUP,
            other_principal=other_principal or DEFAULT_RUNNER_OTHER_PRINCIPAL,
        )
        if outcome == "ok":
            successes += 1
        elif outcome == "cost":
            cost_skips.append(fixture.id)
        else:
            failures.append((fixture.id, outcome))

    logger.info(
        "Done. recorded=%d skipped=%d cost_skipped=%d failed=%d",
        successes,
        skipped,
        len(cost_skips),
        len(failures),
    )
    for fixture_id, reason in failures:
        logger.error("FAILED %s: %s", fixture_id, reason)
    for fixture_id in cost_skips:
        logger.warning("COST-CAPPED %s (no expected.json written)", fixture_id)

    if failures or cost_skips:
        return 1
    return 0


def _require_dataset(fixture: Fixture, dataset_fqdn: str | None) -> None:
    """Raise if a fixture with setup.sql lacks a provisioned dataset."""
    if dataset_fqdn is None:  # pragma: no cover - guarded by needs_dataset
        msg = f"{fixture.id}: setup.sql is present but no dataset was provisioned"
        raise RuntimeError(msg)


def _build_job_config(
    fixture: Fixture,
    ctx: PlaceholderContext,
    bigquery: Any,
) -> Any | None:
    """Construct a ``QueryJobConfig`` from the fixture's parameters + job_config.

    Returns ``None`` when neither ``parameters.json`` nor
    ``job_config.json`` is present so the recorder submits the SQL
    with the BQ client's default config, identical to the pre-P2.e
    behaviour for the parameterless fixtures.

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
      attribute.

    Both payloads are round-tripped through
    :func:`substitute_in_json` so ``${…}`` placeholders inside
    values are expanded before submission.
    """
    if fixture.parameters is None and fixture.job_config is None:
        return None

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


#: Recognises ``POST /bigquery/v2/projects/<p>/datasets`` so the recorder
#: can track secondary datasets created via setup_rest.json and delete
#: them on teardown (no recorder is allowed to leave a dataset behind).
_DATASET_CREATE_PATH = re.compile(r"^/bigquery/v2/projects/(?P<project>[^/]+)/datasets/?$")


def _apply_setup_rest_bq(
    client: Any,
    operations: tuple[dict[str, object], ...],
    ctx: PlaceholderContext,
) -> list[tuple[str, str]]:
    """Apply ``setup_rest.json`` operations against BigQuery's REST API.

    The recorder authenticates via the BigQuery client's authorized
    HTTP session (``client._http``), so REST calls inherit ADC
    automatically. Placeholders in ``path`` and ``body`` are expanded
    using the recorder's ``ctx`` (which carries the operator's
    ``${PRINCIPAL}`` and ``${GROUP}``). The first non-2xx response
    raises so a fixture's REST setup fails loudly rather than leaving
    a half-configured dataset behind.

    Returns the list of ``(project, dataset_id)`` pairs created by the
    setup; the caller is responsible for deleting these on teardown
    so secondary datasets used by authorized-view fixtures don't
    survive a recording session.
    """
    http = client._http  # noqa: SLF001 — using the BQ client's AuthorizedSession
    created: list[tuple[str, str]] = []
    for raw in operations:
        method = str(raw["method"]).upper()
        path = substitute_placeholders(str(raw["path"]), ctx)
        body_raw = raw.get("body")
        body = substitute_in_json(body_raw, ctx) if body_raw is not None else None
        url = f"{BQ_REST_BASE}{path}"
        response = http.request(method=method, url=url, json=body)
        if response.status_code >= _HTTP_SUCCESS_CEILING:
            msg = (
                f"setup_rest.json {method} {path} returned {response.status_code}: {response.text}"
            )
            raise RuntimeError(msg)
        tracked = _track_dataset_creation_bq(method, path, body)
        if tracked is not None:
            created.append(tracked)
    return created


def _track_dataset_creation_bq(
    method: str,
    path: str,
    body: object,
) -> tuple[str, str] | None:
    """Detect ``POST /projects/<p>/datasets`` and return the new (project, id).

    Mirrors the runner-side tracker so the recorder can clean up any
    auxiliary datasets it created during setup_rest.
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


def _record_one(  # noqa: PLR0915 — the recorder body is a linear pipeline
    fixture: Fixture,
    *,
    client: Any,
    run_id: str,
    byte_cap: int,
    dry_run: bool,
    principal: str,
    group: str,
    other_principal: str,
) -> str:
    """Record one fixture.

    Returns ``"ok"`` on success (rows or recorded error envelope),
    ``"cost"`` when the cost guard tripped on a success recording, or
    a free-form error string on any other failure (setup error, dry
    run, recorder bug).
    """
    from google.api_core.exceptions import GoogleAPIError
    from google.cloud import bigquery

    project = client.project
    dataset_fqdn: str | None = None
    if fixture.needs_dataset:
        dataset_name = f"bqemu_conformance_{run_id}_{fixture.name[:16]}".lower()
        dataset_fqdn = f"{project}.{dataset_name}"
        if not dry_run:
            client.create_dataset(bigquery.Dataset(dataset_fqdn), exists_ok=True)

    ctx = PlaceholderContext(
        dataset=dataset_fqdn or f"{project}.bqemu_unused_dataset",
        principal=principal,
        group=group,
        other_principal=other_principal,
    )

    rest_created_datasets: list[tuple[str, str]] = []
    try:
        if fixture.setup_sql is not None:
            _require_dataset(fixture, dataset_fqdn)
            setup_sql = substitute_placeholders(fixture.setup_sql, ctx)
            for stmt in split_statements(setup_sql):
                logger.debug("[setup %s] %s", fixture.id, stmt.splitlines()[0])
                if not dry_run:
                    try:
                        client.query(stmt).result()
                    except GoogleAPIError as exc:
                        # Setup failures are NOT a recordable error fixture
                        # outcome — they break the fixture's precondition.
                        logger.exception("[fail] %s: setup error", fixture.id)
                        return f"setup BigQuery error: {exc}"

        if fixture.setup_rest:
            _require_dataset(fixture, dataset_fqdn)
            if not dry_run:
                try:
                    rest_created_datasets = _apply_setup_rest_bq(client, fixture.setup_rest, ctx)
                except RuntimeError as exc:
                    logger.exception("[fail] %s: REST setup error", fixture.id)
                    return f"setup REST error: {exc}"

        query_sql = substitute_placeholders(fixture.query_sql, ctx)
        job_config = _build_job_config(fixture, ctx, bigquery)

        if dry_run:
            logger.info("[dry-run] %s (would record)", fixture.id)
            outcome = "ok"
        else:
            start_wall = time.perf_counter()
            try:
                if job_config:
                    job = client.query(query_sql, job_config=job_config)
                else:
                    job = client.query(query_sql)
                result = job.result()
            except GoogleAPIError as exc:
                # Error-shape recording path (P3.a). BigQuery raised on
                # the canonical query.sql — write the error envelope
                # instead of rows + schema.
                wall_ms = int((time.perf_counter() - start_wall) * 1000)
                payload = _build_error_payload(
                    fixture=fixture,
                    exc=exc,
                    project=project,
                    location=client.location or "",
                    dataset_fqdn=dataset_fqdn,
                    wall_ms=wall_ms,
                )
                fixture.expected_path.write_text(
                    json.dumps(payload, indent=2, sort_keys=False) + "\n",
                    encoding="utf-8",
                )
                logger.info(
                    "[ok-error] %s (job=%s reason=%s http=%s wall=%dms)",
                    fixture.id,
                    payload["bigquery"]["job_id"],
                    payload["error"].get("reason"),
                    payload["error"].get("http_status"),
                    wall_ms,
                )
                outcome = "ok"
            else:
                wall_ms = int((time.perf_counter() - start_wall) * 1000)
                bytes_processed = int(job.total_bytes_processed or 0)
                if bytes_processed > byte_cap:
                    logger.warning(
                        "%s scanned %d bytes (> cap %d); skipping write",
                        fixture.id,
                        bytes_processed,
                        byte_cap,
                    )
                    outcome = "cost"
                else:
                    rows = _result_to_rows(result)
                    schema = _result_to_schema(result)
                    payload: dict[str, Any] = {
                        "fixture_version": FIXTURE_VERSION,
                        "bigquery": {
                            "project": project,
                            "job_id": job.job_id,
                            "location": job.location or client.location or "",
                            "total_bytes_processed": bytes_processed,
                            "total_bytes_billed": int(job.total_bytes_billed or 0),
                            "duration_ms": wall_ms,
                        },
                        "schema": schema,
                        "rows": rows,
                        "row_count": len(rows),
                        "duration_class": _duration_class(wall_ms),
                    }
                    # P7.a — response-object equivalence. When a fixture
                    # carries job_config.json, also capture the
                    # high-severity job-statistics fields so the runner
                    # can diff them. Each field is only written when
                    # the BigQuery client surfaces a non-None value so
                    # the recorded payload stays minimal for fixtures
                    # that don't care.
                    job_metadata = _extract_job_metadata(job)
                    if job_metadata:
                        payload["job_metadata"] = job_metadata
                    fixture.expected_path.write_text(
                        json.dumps(payload, indent=2, sort_keys=False) + "\n",
                        encoding="utf-8",
                    )
                    logger.info(
                        "[ok] %s (job=%s rows=%d bytes=%d wall=%dms)",
                        fixture.id,
                        job.job_id,
                        len(rows),
                        bytes_processed,
                        wall_ms,
                    )
                    outcome = "ok"
    except Exception as exc:
        logger.exception("[fail] %s: recorder error", fixture.id)
        outcome = f"recorder error: {type(exc).__name__}: {exc}"
    finally:
        if not dry_run:
            for proj_id, ds_id in reversed(rest_created_datasets):
                try:
                    client.delete_dataset(
                        f"{proj_id}.{ds_id}",
                        delete_contents=True,
                        not_found_ok=True,
                    )
                except GoogleAPIError:  # pragma: no cover - cleanup-only
                    logger.warning("Failed to delete dataset %s.%s", proj_id, ds_id)
            if dataset_fqdn is not None:
                try:
                    client.delete_dataset(dataset_fqdn, delete_contents=True, not_found_ok=True)
                except GoogleAPIError:  # pragma: no cover - cleanup-only
                    logger.warning("Failed to delete dataset %s", dataset_fqdn)
    return outcome


def _build_error_payload(
    *,
    fixture: Fixture,  # noqa: ARG001 — part of the signature so callers can keep the same kwargs
    exc: Any,
    project: str,
    location: str,
    dataset_fqdn: str | None,
    wall_ms: int,
) -> dict[str, Any]:
    """Render a recorded error envelope for an ``expected.json`` write.

    The error envelope is the P3.a addition to the v2 fixture shape
    (ADR 0022 §3 ``Error parity``). It mirrors the success envelope's
    ``bigquery`` block where possible (the failed query may not have
    produced a job id; the recorder uses ``None`` when absent) and
    adds an ``error`` block carrying the four diff-relevant fields the
    runner matches against. The free-form
    ``error.message_sample`` preserves the raw recorded BigQuery
    wording for audit so a future re-record diff is human-readable.
    """
    reason: str | None = None
    location_field: str | None = None
    http_status: int | None = None
    message: str = str(exc)

    errors_attr = getattr(exc, "errors", None)
    if errors_attr:
        first = errors_attr[0]
        if isinstance(first, dict):
            reason = first.get("reason")
            location_field = first.get("location")
            err_message = first.get("message")
            if err_message:
                message = err_message
    else:
        message_attr = getattr(exc, "message", None)
        if message_attr:
            message = str(message_attr)

    code_attr = getattr(exc, "code", None)
    if isinstance(code_attr, int):
        http_status = code_attr

    job_id = _extract_job_id(exc)

    return {
        "fixture_version": FIXTURE_VERSION,
        "bigquery": {
            "project": project,
            "job_id": job_id,
            "location": location,
            "total_bytes_processed": 0,
            "total_bytes_billed": 0,
            "duration_ms": wall_ms,
        },
        "error": {
            "reason": reason,
            "location": location_field,
            "http_status": http_status,
            "message_pattern": _build_message_pattern(message, dataset_fqdn),
            "message_sample": message,
        },
        "duration_class": _duration_class(wall_ms),
    }


def _extract_job_id(exc: Any) -> str | None:
    """Best-effort extraction of the failing BigQuery job id from a raised error.

    The ``google-cloud-bigquery`` client sometimes attaches the
    failed job to the raised exception via ``__cause__`` or the
    underlying ``response`` payload; when it doesn't, returns
    ``None``. The runner does not use the job id for comparison —
    only for the diagnostic message when a diff fails.
    """
    job_attr = getattr(exc, "job_id", None)
    if isinstance(job_attr, str):
        return job_attr
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 — defensive; recorder fallback
            return None
        if isinstance(body, dict):
            error = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
            for nested_error in error.get("errors", []) if isinstance(error, dict) else []:
                if isinstance(nested_error, dict) and "debugInfo" in nested_error:
                    debug = str(nested_error["debugInfo"])
                    match = re.search(r"job_id=([\w\-]+)", debug)
                    if match:
                        return match.group(1)
    return None


#: Recorded ``message_pattern`` substitutes the per-fixture dataset
#: FQDN back to a wildcard so re-recordings against different
#: projects stay stable. BigQuery's error messages reference the
#: dataset in two forms (``project.dataset`` and the colon-separated
#: ``project:dataset``); both are normalised. Line:column markers
#: (``[12:34]``) drift between recorder runs even when the SQL is
#: unchanged, so they are replaced with a digit-range pattern.
_LINE_COLUMN_RE = re.compile(r"\[\d+:\d+\]")
_DATASET_PLACEHOLDER = "__BQEMU_DATASET_PLACEHOLDER__"
_LINE_COLUMN_PLACEHOLDER = "__BQEMU_LINE_COLUMN_PLACEHOLDER__"


def _build_message_pattern(message: str, dataset_fqdn: str | None) -> str:
    """Build a regex pattern for a BigQuery error message.

    The recorder writes a pattern, not the literal message, so a
    re-record against a different project (or with a slightly
    different job context) does not break parity. Substitutions:

    1. The per-fixture dataset FQDN — in both ``project.dataset``
       and ``project:dataset`` forms — is replaced with a regex
       matching any dataset-shaped token. The runner's emulator
       dataset will have a different name; this wildcard absorbs the
       drift.
    2. Line:column markers (``[12:34]``) are replaced with a
       digit-range pattern. BigQuery's parser sometimes emits the
       marker; DuckDB-via-SQLGlot may not, and even when it does the
       column can drift by one character if the rewriter expands a
       function call.
    3. Every other character is regex-escaped so the pattern matches
       the literal recorded wording.

    The author can edit the recorded pattern post-recording to widen
    a wording that drifts across BigQuery releases — the framework
    treats the recorded pattern as authoritative.
    """
    normalised = message
    if dataset_fqdn is not None:
        bq_form = dataset_fqdn.replace(".", ":", 1)
        # Substitute the longer form first so a project.dataset substring
        # of project:dataset doesn't pre-empt the replacement.
        for form in sorted({dataset_fqdn, bq_form}, key=len, reverse=True):
            normalised = normalised.replace(form, _DATASET_PLACEHOLDER)
    normalised = _LINE_COLUMN_RE.sub(_LINE_COLUMN_PLACEHOLDER, normalised)
    escaped = re.escape(normalised)
    return escaped.replace(re.escape(_DATASET_PLACEHOLDER), r"[\w\-\.:]+").replace(
        re.escape(_LINE_COLUMN_PLACEHOLDER), r"\[\d+:\d+\]"
    )


def _result_to_rows(result: Any) -> list[dict[str, Any]]:
    """Encode a BigQuery ``QueryResult`` as recorder-shaped rows.

    Shares the encoder with the runner so the recorded payload and the
    runner's actual output use the same JSON conventions.
    """
    rows: list[dict[str, Any]] = []
    for row in result:
        encoded: dict[str, Any] = {}
        for field_def, value in zip(result.schema, row.values(), strict=True):
            encoded[field_def.name] = row_to_jsonable(value, field_def)
        rows.append(encoded)
    return rows


def _result_to_schema(result: Any) -> list[dict[str, Any]]:
    """Encode a BigQuery schema as recorder-shaped JSON."""
    return [field_to_jsonable(f) for f in result.schema]


def _duration_class(wall_ms: int) -> str:
    """Categorise wall-clock duration coarsely.

    The class is informational; the runner does not use it for
    comparison. Operators consult it when triaging slow re-records.
    """
    if wall_ms < DURATION_FAST_MS:
        return "fast"
    if wall_ms < DURATION_MEDIUM_MS:
        return "medium"
    return "slow"


def _extract_job_metadata(job: Any) -> dict[str, Any]:
    """Capture the high-severity response-object fields from a finished QueryJob.

    Returns a dict the recorder writes under ``expected.json``'s
    ``job_metadata`` key. The runner's comparator (in
    :mod:`tests.conformance._comparison`) diffs ONLY the keys that
    are present in the expected payload, so this helper writes
    minimally — a field absent from the recorded baseline is not
    asserted against.

    Field selection follows the **Tier 1** severity rows from
    [`api-configuration-coverage-matrix`](../../docs/reference/api-configuration-coverage-matrix.md):
    ``cache_hit``, ``statement_type``, ``num_dml_affected_rows``,
    ``ddl_operation_performed``. Timing fields are excluded
    deliberately — they're too noisy to compare across runs.
    """
    metadata: dict[str, Any] = {}
    # ``cache_hit`` is informational but cheap to capture; useful for
    # documenting the emulator's "always false" divergence.
    if job.cache_hit is not None:
        metadata["cache_hit"] = bool(job.cache_hit)
    # ``statement_type`` is dispatch-critical (clients route on this
    # to decide whether to fetch rows or just inspect numDmlAffectedRows).
    if job.statement_type:
        metadata["statement_type"] = str(job.statement_type)
    # DML row count — critical for INSERT/UPDATE/DELETE/MERGE correctness.
    if job.num_dml_affected_rows is not None:
        metadata["num_dml_affected_rows"] = int(job.num_dml_affected_rows)
    # DDL operation indicator — critical for catalog-state correctness.
    if job.ddl_operation_performed:
        metadata["ddl_operation_performed"] = str(job.ddl_operation_performed)
    return metadata


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project",
        required=True,
        help="BigQuery project to bill the recording jobs to.",
    )
    parser.add_argument(
        "--location",
        default="US",
        help="BigQuery dataset location (default: US).",
    )
    parser.add_argument(
        "--byte-cap",
        type=int,
        default=DEFAULT_BYTE_CAP,
        help=(
            f"Refuse any fixture scanning more than this many bytes (default: {DEFAULT_BYTE_CAP})."
        ),
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Substring match on '<phase>/<name>' to limit which fixtures are recorded.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing expected.json files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the recording plan without running queries.",
    )
    parser.add_argument(
        "--corpus-dir",
        default=str(CORPUS_DIR),
        help="Override the corpus directory (default: tests/conformance/sql_corpus).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    raise SystemExit(main())
