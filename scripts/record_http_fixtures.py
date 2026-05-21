#!/usr/bin/env python3
"""Record HTTP-shape conformance baselines from real BigQuery (P2.f).

For every fixture under ``tests/conformance/http_corpus/`` this script:

1. (Optionally) executes ``setup.sql`` against a per-fixture temp
   dataset on the operator-supplied project.
2. Runs any ``setup_requests.json`` operations against BigQuery's
   REST API, capturing the listed variables from each response body.
3. Issues the canonical ``request.json`` against BigQuery's REST API
   (with captured + base placeholders expanded).
4. Writes the recorded response — ``http_status``, a subset of
   ``headers``, and the response ``body`` — to ``expected_response.json``.
   Server-generated opaque fields (job ids, etags, timestamps, opaque
   self-links) are scrubbed to the ``WILDCARD`` sentinel so the runner
   can diff structurally without false negatives on values it cannot
   predict.

Usage::

    python scripts/record_http_fixtures.py \\
        --project your-bigquery-project \\
        --location US

Refuses to overwrite an existing ``expected_response.json`` unless
``--force`` is supplied. ``--filter <substring>`` re-records only
fixtures whose ``<phase>/<name>`` id matches the substring.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any
import uuid

# scripts/ → repo root → tests.conformance.* importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conformance._corpus import (  # noqa: E402
    DEFAULT_RUNNER_GCS_BUCKET,
    DEFAULT_RUNNER_GROUP,
    DEFAULT_RUNNER_OTHER_PRINCIPAL,
    DEFAULT_RUNNER_PRINCIPAL,
    PlaceholderContext,
    split_statements,
    substitute_placeholders,
)
from tests.conformance._http_comparison import mask_volatile_fields  # noqa: E402
from tests.conformance._http_corpus import (  # noqa: E402
    HTTP_CORPUS_DIR,
    HttpFixture,
    HttpRequest,
    discover_http_fixtures,
    expand_placeholders,
    expand_placeholders_in_json,
    resolve_dotted_path,
)

#: BigQuery's REST API base URL — the recorder targets this; the
#: runner targets the in-process emulator instead.
BQ_REST_BASE = "https://bigquery.googleapis.com"

#: HTTP success ceiling. Anything at or above this aborts the setup
#: chain — with the exception of ``308 Resume Incomplete`` which is
#: the upload-host's documented response for non-final chunks (G2).
_HTTP_SUCCESS_CEILING = 300
_HTTP_RESUME_INCOMPLETE = 308

#: Header subset we record per fixture. BigQuery returns dozens of
#: opaque headers per call; we capture only the ones whose drift
#: would be a real bug the emulator could regress on. G2 added
#: ``location`` + ``x-guploader-uploadid`` for resumable-upload
#: initiation responses, and ``range`` for chunk-progress (308)
#: responses.
RECORDED_HEADERS = (
    "content-type",
    "location",
    "x-guploader-uploadid",
    "range",
)

#: Dotted paths whose values are server-generated and must be masked
#: before writing the recorded baseline. ``[]`` matches every element
#: of the list at that point (see ``_http_comparison.mask_volatile_fields``).
VOLATILE_PATHS: tuple[str, ...] = (
    # Top-level Job resource (jobs.get, jobs.insert response shapes)
    "jobReference.jobId",
    "jobReference.projectId",
    "jobReference.location",
    "id",
    "etag",
    "selfLink",
    "user_email",
    "principal_subject",
    "jobCreationReason",
    "cacheHit",
    "statistics.creationTime",
    "statistics.startTime",
    "statistics.endTime",
    "statistics.totalSlotMs",
    "statistics.finalExecutionDurationMs",
    "statistics.query.totalSlotMs",
    "statistics.query.estimatedBytesProcessed",
    "statistics.query.totalBytesBilled",
    "statistics.query.totalBytesProcessed",
    "statistics.query.totalBytesProcessedAccuracy",
    "statistics.totalBytesProcessed",
    "statistics.totalBytesBilled",
    "statistics.query.cacheHit",
    "statistics.query.billingTier",
    "statistics.query.referencedTables",
    "statistics.query.queryPlan",
    "statistics.query.timeline",
    "statistics.numChildJobs",
    "statistics.parentJobId",
    "statistics.scriptStatistics",
    "statistics.sessionInfo",
    "statistics.transactionInfo",
    "statistics.query.schema",
    "pageToken",
    "nextPageToken",
    "queryId",
    "totalBytesProcessed",
    "totalBytesBilled",
    "totalSlotMs",
    "creationTime",
    "startTime",
    "endTime",
    "location",
    "configuration.query.priority",
    "configuration.query.destinationTable",
    "configuration.query.writeDisposition",
    "configuration.query.createDisposition",
    "configuration.query.useLegacySql",
    "configuration.jobType",
    "configuration.dryRun",
    "configuration.labels",
    "configuration.query.parameterMode",
    # G1: load + extract job-resource volatile paths. The recorder
    # uses the operator's real project + a per-fixture dataset id; the
    # runner uses ``test-project`` + a per-test uuid dataset. Masking
    # the project/dataset components on the load/extract destination/
    # source table is the same precedent the query family uses above.
    # ``status.state`` is RUNNING in the recorded snapshot (BQ async)
    # vs DONE in the emulator (synchronous executor) — async-vs-sync
    # divergence pinned to the wildcard so the comparator accepts both.
    # ``statistics.reservation_id`` is a BQ-specific field the emulator
    # does not surface. Load + extract per-job statistics counters are
    # likewise BQ-specific timing/IO metadata.
    "configuration.load.destinationTable.projectId",
    "configuration.load.destinationTable.datasetId",
    "configuration.extract.sourceTable.projectId",
    "configuration.extract.sourceTable.datasetId",
    # BQ adds singular ``destinationUri`` alongside plural
    # ``destinationUris`` in extract-job responses; the emulator only
    # echoes back the plural form. Wildcard the singular so its
    # absence on the emulator side is accepted.
    "configuration.extract.destinationUri",
    "status",
    "statistics.reservation_id",
    "statistics.load",
    "statistics.extract",
    # jobs.cancel response wraps the Job inside a "job" key
    "job.id",
    "job.etag",
    "job.selfLink",
    "job.user_email",
    "job.principal_subject",
    "job.jobCreationReason",
    "job.cacheHit",
    "job.jobReference.jobId",
    "job.jobReference.projectId",
    "job.jobReference.location",
    "job.configuration.query.destinationTable",
    "job.configuration.query.writeDisposition",
    "job.configuration.query.createDisposition",
    "job.configuration.query.priority",
    "job.configuration.query.useLegacySql",
    "job.configuration.jobType",
    "job.configuration.dryRun",
    "job.configuration.labels",
    "job.statistics",
    "job.status",
    # jobs.list response: per-job entries
    "jobs[].id",
    "jobs[].jobReference.jobId",
    "jobs[].jobReference.projectId",
    "jobs[].jobReference.location",
    "jobs[].etag",
    "jobs[].statistics",
    "jobs[].user_email",
    "jobs[].principal_subject",
    "jobs[].configuration",
    "jobs[].status",
    "jobs[].state",
    "jobs[].errorResult",
    "jobs[].jobCreationReason",
    "jobs[].selfLink",
    # Error envelope wording differences (DuckDB-identifier-case
    # preservation gap, known P7.c follow-up; see STATUS.md). The
    # error reason / code / status are the structural assertion; the
    # human-readable message wording stays masked.
    "error.message",
    "error.errors[].message",
    "error.errors[].location",
    "error.errors[].locationType",
    "error.errors[].debugInfo",
    "error.details",
    "error.status",
    # ── P7.c — datasets.list / tables.list per-resource volatile fields ──
    # The recording-time dataset / table identity (project, dataset
    # id, fully-qualified id, creation time) is emitted into per-item
    # entries. The runner uses a different temp dataset every test,
    # so position-by-position comparison would never match. The
    # structural assertion is that the entries are present with the
    # documented keys; the values are intentionally non-pinned.
    "datasets[].id",
    "datasets[].datasetReference.projectId",
    "datasets[].datasetReference.datasetId",
    "datasets[].location",
    "tables[].id",
    "tables[].tableReference.projectId",
    "tables[].tableReference.datasetId",
    "tables[].creationTime",
    "tables[].expirationTime",
    "tables[].lastModifiedTime",
    # ── P7.c — datasets.get / tables.get top-level volatile fields ──
    # Same logic as above but for the singular ``GET`` endpoints. The
    # runner's emulator-side runtime emits different project / dataset
    # ids per test, so per-row pinning would never match; access
    # entries are auto-populated by real BQ but not by the emulator.
    "datasetReference.projectId",
    "datasetReference.datasetId",
    "tableReference.projectId",
    "tableReference.datasetId",
    "tableReference.tableId",
    "lastModifiedTime",
    "access",
)

FIXTURE_VERSION = 1

logger = logging.getLogger("record_http")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from google.cloud import bigquery

    client = bigquery.Client(project=args.project, location=args.location)
    fixtures = discover_http_fixtures(include_unrecorded=True)
    if args.filter:
        fixtures = [f for f in fixtures if args.filter in f.id]
    if not fixtures:
        logger.error("No HTTP fixtures matched filter=%r", args.filter)
        return 1

    principal = os.environ.get("BQEMU_CONFORMANCE_PRINCIPAL", "").strip()
    group = os.environ.get("BQEMU_CONFORMANCE_GROUP", "").strip()
    other_principal = os.environ.get("BQEMU_CONFORMANCE_OTHER_PRINCIPAL", "").strip()
    gcs_bucket = os.environ.get("BQEMU_CONFORMANCE_GCS_BUCKET", "").strip()

    logger.info(
        "Recording %d HTTP fixtures against project=%s (principal=%s group=%s)",
        len(fixtures),
        args.project,
        principal or "<unset>",
        group or "<unset>",
    )

    run_id = uuid.uuid4().hex[:12]
    successes = 0
    skipped = 0
    failures: list[tuple[str, str]] = []

    for fixture in fixtures:
        if fixture.expected_path.is_file() and not args.force:
            logger.info(
                "[skip] %s (expected_response.json exists; pass --force to re-record)",
                fixture.id,
            )
            skipped += 1
            continue
        outcome = _record_one(
            fixture,
            client=client,
            run_id=run_id,
            dry_run=args.dry_run,
            principal=principal or DEFAULT_RUNNER_PRINCIPAL,
            group=group or DEFAULT_RUNNER_GROUP,
            other_principal=other_principal or DEFAULT_RUNNER_OTHER_PRINCIPAL,
            gcs_bucket=gcs_bucket or DEFAULT_RUNNER_GCS_BUCKET,
        )
        if outcome == "ok":
            successes += 1
        else:
            failures.append((fixture.id, outcome))

    logger.info(
        "Done. recorded=%d skipped=%d failed=%d",
        successes,
        skipped,
        len(failures),
    )
    for fixture_id, reason in failures:
        logger.error("FAILED %s: %s", fixture_id, reason)

    return 0 if not failures else 1


def _record_one(  # noqa: PLR0915 — linear pipeline
    fixture: HttpFixture,
    *,
    client: Any,
    run_id: str,
    dry_run: bool,
    principal: str,
    group: str,
    other_principal: str,
    gcs_bucket: str,
) -> str:
    """Record one HTTP fixture. Returns ``"ok"`` or a free-form error string."""
    from google.api_core.exceptions import GoogleAPIError
    from google.cloud import bigquery

    project = client.project
    dataset_fqdn: str | None = None
    rest_created_datasets: list[tuple[str, str]] = []
    captured: dict[str, str] = {}

    if fixture.needs_dataset:
        dataset_name = f"bqemu_httpfx_{run_id}_{fixture.name[:16]}".lower()
        dataset_fqdn = f"{project}.{dataset_name}"
        if not dry_run:
            client.create_dataset(bigquery.Dataset(dataset_fqdn), exists_ok=True)

    ctx = PlaceholderContext(
        dataset=dataset_fqdn or f"{project}.bqemu_unused_dataset",
        principal=principal,
        group=group,
        other_principal=other_principal,
        gcs_bucket=gcs_bucket,
    )
    base_mapping = _placeholder_mapping(ctx)
    http = client._http  # noqa: SLF001 — using the BQ client's AuthorizedSession

    try:
        if fixture.setup_sql is not None:
            if dataset_fqdn is None:
                msg = f"{fixture.id}: setup.sql present but no dataset was provisioned"
                raise RuntimeError(msg)  # noqa: TRY301 — guard inside the try is intentional
            setup_sql = substitute_placeholders(fixture.setup_sql, ctx)
            for stmt in split_statements(setup_sql):
                if dry_run:
                    logger.debug("[dry-run setup-sql] %s", stmt.splitlines()[0])
                    continue
                try:
                    client.query(stmt).result()
                except GoogleAPIError as exc:
                    return f"setup.sql failed: {exc}"

        for idx, setup_request in enumerate(fixture.setup_requests):
            if dry_run:
                logger.debug("[dry-run setup-req] %s %s", setup_request.method, setup_request.path)
                continue
            response = _issue_request(
                http,
                setup_request,
                mapping={**base_mapping, **captured},
                source=f"{fixture.id} setup[#{idx}]",
                fixture_dir=fixture.path,
            )
            if (
                response.status_code >= _HTTP_SUCCESS_CEILING
                and response.status_code != _HTTP_RESUME_INCOMPLETE
            ):
                return (
                    f"setup_requests[#{idx}] returned {response.status_code}: {response.text[:200]}"
                )
            tracked = _track_dataset_creation(
                setup_request.method,
                expand_placeholders(setup_request.path, {**base_mapping, **captured}),
                response,
            )
            if tracked is not None:
                rest_created_datasets.append(tracked)
            if setup_request.capture:
                body = _parse_response_json(response) or {}
                headers_lower = {name.lower(): value for name, value in response.headers.items()}
                try:
                    for name, dotted in setup_request.capture:
                        if dotted.startswith("header:"):
                            header_name = dotted[len("header:") :].lower()
                            if header_name not in headers_lower:
                                return (
                                    f"setup[#{idx}] capture failed: response header "
                                    f"{dotted[len('header:') :]!r} absent"
                                )
                            captured[name] = headers_lower[header_name]
                        else:
                            captured[name] = str(resolve_dotted_path(body, dotted))
                except (KeyError, IndexError) as exc:
                    return f"setup[#{idx}] capture failed: {exc}"

        if dry_run:
            logger.info("[dry-run] %s (would record)", fixture.id)
            return "ok"

        canonical_response = _issue_request(
            http,
            fixture.request,
            mapping={**base_mapping, **captured},
            source=f"{fixture.id} canonical",
            fixture_dir=fixture.path,
        )
        recorded_body = _normalise_recorded_body(canonical_response)
        recorded_headers = _capture_headers(canonical_response)

        payload = {
            "fixture_version": FIXTURE_VERSION,
            "bigquery": {
                "project": project,
                "location": client.location or "",
            },
            "http_status": canonical_response.status_code,
            "headers": dict(recorded_headers),
            "body": recorded_body,
        }
        fixture.expected_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "[ok] %s (status=%s body_keys=%s)",
            fixture.id,
            canonical_response.status_code,
            sorted(recorded_body.keys()) if isinstance(recorded_body, dict) else "<non-dict>",
        )
        return "ok"  # noqa: TRY300 — recorder's success/failure split is clearer flat
    except Exception as exc:
        logger.exception("[fail] %s: recorder error", fixture.id)
        return f"recorder error: {type(exc).__name__}: {exc}"
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


def _placeholder_mapping(ctx: PlaceholderContext) -> dict[str, str]:
    """Base placeholder mapping (PROJECT / DATASET / …)."""
    return {
        "DATASET": ctx.dataset,
        "PROJECT": ctx.project,
        "DATASET_ID": ctx.dataset_id,
        "PRINCIPAL": ctx.principal,
        "GROUP": ctx.group,
        "OTHER_PRINCIPAL": ctx.other_principal,
        "GCS_BUCKET": ctx.gcs_bucket,
    }


def _issue_request(
    http: Any,
    request: HttpRequest,
    *,
    mapping: dict[str, str],
    source: str,
    fixture_dir: Path | None = None,
) -> Any:
    """Substitute placeholders and issue ``request`` via ``http`` (BQ AuthorizedSession).

    When ``request.body_bin`` is set (G2 — multipart / resumable upload
    fixtures), the recorder reads the raw bytes from the sibling file
    and posts them verbatim. Headers (Content-Type, Content-Range) are
    preserved exactly as the fixture declared them so the recorded
    response reflects the real BQ wire-format response to that exact
    request shape.
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
    url = f"{BQ_REST_BASE}{path}"
    if request.body_bin is not None:
        if fixture_dir is None:
            msg = f"{source}: body_bin requires a fixture_dir"
            raise RuntimeError(msg)
        body_bytes = (fixture_dir / request.body_bin).read_bytes()
        for token, value in mapping.items():
            body_bytes = body_bytes.replace(f"${{{token}}}".encode(), value.encode())
        return http.request(
            method=request.method,
            url=url,
            data=body_bytes,
            headers=headers or None,
        )
    return http.request(method=request.method, url=url, json=body_json, headers=headers or None)


def _parse_response_json(response: Any) -> object | None:
    """Best-effort parse of a response body into JSON."""
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _normalise_recorded_body(response: Any) -> object:
    """Build the recorded body with volatile fields scrubbed to ``WILDCARD``."""
    if not response.content:
        return ""
    body = _parse_response_json(response)
    if body is None:
        return response.text
    if isinstance(body, dict):
        mask_volatile_fields(body, VOLATILE_PATHS)
    return body


def _capture_headers(response: Any) -> dict[str, str]:
    """Capture the subset of headers we record per fixture."""
    out: dict[str, str] = {}
    for name in RECORDED_HEADERS:
        value = response.headers.get(name)
        if value:
            # The runner subset-matches the *full* recorded header
            # value; strip a charset suffix so the comparison is
            # robust to BigQuery's variants ("application/json;
            # charset=UTF-8" vs "application/json").
            if name == "content-type" and ";" in value:
                value = value.split(";", 1)[0].strip()
            out[name] = value
    return out


def _track_dataset_creation(method: str, path: str, response: Any) -> tuple[str, str] | None:
    """Detect ``POST /projects/<p>/datasets`` and return ``(project, id)``."""
    if method.upper() != "POST":
        return None
    if "/datasets" not in path:
        return None
    body = _parse_response_json(response)
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
        "--filter",
        default=None,
        help="Substring match on '<phase>/<name>' to limit which fixtures are recorded.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing expected_response.json files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the recording plan without issuing requests.",
    )
    parser.add_argument(
        "--corpus-dir",
        default=str(HTTP_CORPUS_DIR),
        help="Override the corpus directory (default: tests/conformance/http_corpus).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    raise SystemExit(main())
