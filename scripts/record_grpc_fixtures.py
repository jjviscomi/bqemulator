#!/usr/bin/env python3
"""Record gRPC-shape conformance baselines from real BigQuery (P3.d).

For every fixture under ``tests/conformance/grpc_corpus/`` this script:

1. (Optionally) executes ``setup.sql`` against a per-fixture temp
   dataset on the operator-supplied project.
2. (Optionally) runs any ``setup_requests.json`` operations against
   BigQuery's REST API, capturing the listed variables from each
   response body.
3. Opens an authorized gRPC channel to
   ``bigquerystorage.googleapis.com:443`` and issues the canonical
   call sequence in order, substituting captured placeholders.
4. Writes the recorded outcome — for each call: ``method``,
   ``status`` (gRPC status code name), and either ``response``
   (unary) or ``responses`` (server / bidi stream) as
   ``MessageToJson``-shaped dicts — to ``expected_response.json``.
   Server-generated opaque fields (stream names, write-stream
   offsets, Arrow IPC bytes, timestamps) are scrubbed to the
   ``WILDCARD`` sentinel.

Usage::

    python scripts/record_grpc_fixtures.py \
        --project your-bigquery-project \
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
    DEFAULT_RUNNER_GROUP,
    DEFAULT_RUNNER_OTHER_PRINCIPAL,
    DEFAULT_RUNNER_PRINCIPAL,
    PlaceholderContext,
    split_statements,
    substitute_placeholders,
)
from tests.conformance._grpc_comparison import mask_volatile_fields  # noqa: E402
from tests.conformance._grpc_corpus import (  # noqa: E402
    GRPC_CORPUS_DIR,
    GRPC_SERVICE_PATHS,
    GrpcCall,
    GrpcFixture,
    deserialize_response,
    discover_grpc_fixtures,
    expand_placeholders,
    expand_placeholders_in_json,
    proto_to_dict,
    resolve_dotted_path,
    serialize_request,
)
from tests.conformance._http_corpus import HttpRequest  # noqa: E402

#: BigQuery's REST API base URL (setup phase only).
BQ_REST_BASE = "https://bigquery.googleapis.com"

#: BigQuery Storage API gRPC endpoint.
BQ_STORAGE_GRPC_TARGET = "bigquerystorage.googleapis.com:443"

#: HTTP success ceiling — any status code at or above this aborts the
#: setup chain.
_HTTP_SUCCESS_CEILING = 300

#: Dotted paths whose values are server-generated and must be masked
#: before writing the recorded baseline. ``[]`` matches every element
#: of a list at that point (see ``_grpc_comparison.mask_volatile_fields``).
#: The set is split per response shape; the recorder applies the
#: union to every response so volatile-key collisions across messages
#: get scrubbed consistently.
VOLATILE_PATHS: tuple[str, ...] = (
    # ReadSession / SplitReadStreamResponse — opaque names + arrow
    # bytes + server-generated timestamps + size estimates. The
    # ``avro_schema.schema`` mask matches the schema-once-on-session
    # contract for Avro fixtures (G3 / ADR 0030).
    "name",
    "session.name",
    "expire_time",
    "streams[].name",
    "primary_stream.name",
    "remainder_stream.name",
    "arrow_schema.serialized_schema",
    "avro_schema.schema",
    "estimated_total_bytes_scanned",
    "estimated_total_physical_file_size",
    "estimated_row_count",
    "trace_id",
    # ReadRowsResponse — bytes + timing.
    "arrow_record_batch.serialized_record_batch",
    "avro_rows.serialized_binary_rows",
    "stats.progress.at_response_start",
    "stats.progress.at_response_end",
    "stats.throttle_state.throttle_percent",
    # WriteStream — opaque names + timestamps + creation-time fields.
    "create_time",
    "commit_time",
    "flush_time",
    "table_schema",
    # AppendRowsResponse — offset is deterministic per fixture; we
    # let the comparator catch any drift there. Schema-update and
    # write_stream-name fields stay masked because real BQ omits
    # them in steady state.
    "updated_schema",
    "write_stream",
    # BatchCommitWriteStreamsResponse error envelope is the most
    # likely shape variance the emulator could drift on; the entity
    # name carries server identity that won't match.
    "stream_errors[].entity",
)

FIXTURE_VERSION = 1

logger = logging.getLogger("record_grpc")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import google.auth
    import google.auth.transport.grpc
    import google.auth.transport.requests
    from google.cloud import bigquery

    credentials, _project = google.auth.default(
        scopes=[
            "https://www.googleapis.com/auth/bigquery",
            "https://www.googleapis.com/auth/cloud-platform",
        ]
    )
    rest_client = bigquery.Client(project=args.project, location=args.location)

    fixtures = discover_grpc_fixtures(include_unrecorded=True)
    if args.filter:
        fixtures = [f for f in fixtures if args.filter in f.id]
    if not fixtures:
        logger.error("No gRPC fixtures matched filter=%r", args.filter)
        return 1

    principal = os.environ.get("BQEMU_CONFORMANCE_PRINCIPAL", "").strip()
    group = os.environ.get("BQEMU_CONFORMANCE_GROUP", "").strip()
    other_principal = os.environ.get("BQEMU_CONFORMANCE_OTHER_PRINCIPAL", "").strip()

    logger.info(
        "Recording %d gRPC fixtures against project=%s (principal=%s group=%s)",
        len(fixtures),
        args.project,
        principal or "<unset>",
        group or "<unset>",
    )

    auth_request = google.auth.transport.requests.Request()
    channel = google.auth.transport.grpc.secure_authorized_channel(
        credentials,
        auth_request,
        BQ_STORAGE_GRPC_TARGET,
        options=[
            ("grpc.max_send_message_length", 64 * 1024 * 1024),
            ("grpc.max_receive_message_length", 64 * 1024 * 1024),
        ],
    )

    run_id = uuid.uuid4().hex[:12]
    successes = 0
    skipped = 0
    failures: list[tuple[str, str]] = []

    try:
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
                rest_client=rest_client,
                grpc_channel=channel,
                run_id=run_id,
                dry_run=args.dry_run,
                principal=principal or DEFAULT_RUNNER_PRINCIPAL,
                group=group or DEFAULT_RUNNER_GROUP,
                other_principal=other_principal or DEFAULT_RUNNER_OTHER_PRINCIPAL,
                project=args.project,
            )
            if outcome == "ok":
                successes += 1
            else:
                failures.append((fixture.id, outcome))
    finally:
        channel.close()
        rest_client.close()

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
    fixture: GrpcFixture,
    *,
    rest_client: Any,
    grpc_channel: Any,
    run_id: str,
    dry_run: bool,
    principal: str,
    group: str,
    other_principal: str,
    project: str,
) -> str:
    """Record one gRPC fixture. Returns ``"ok"`` or a free-form error string."""
    from google.api_core.exceptions import GoogleAPIError
    from google.cloud import bigquery
    import grpc

    dataset_fqdn: str | None = None
    rest_created_datasets: list[tuple[str, str]] = []
    captured: dict[str, str] = {}

    if fixture.needs_dataset:
        dataset_name = f"bqemu_grpcfx_{run_id}_{fixture.name[:16]}".lower()
        dataset_fqdn = f"{project}.{dataset_name}"
        if not dry_run:
            rest_client.create_dataset(bigquery.Dataset(dataset_fqdn), exists_ok=True)

    ctx = PlaceholderContext(
        dataset=dataset_fqdn or f"{project}.bqemu_unused_dataset",
        principal=principal,
        group=group,
        other_principal=other_principal,
    )
    base_mapping = _placeholder_mapping(ctx)
    rest_http = rest_client._http  # noqa: SLF001 — using the BQ client's AuthorizedSession

    try:
        if fixture.setup_sql is not None:
            if dataset_fqdn is None:
                msg = f"{fixture.id}: setup.sql present but no dataset was provisioned"
                raise RuntimeError(msg)  # noqa: TRY301
            setup_sql = substitute_placeholders(fixture.setup_sql, ctx)
            for stmt in split_statements(setup_sql):
                if dry_run:
                    logger.debug("[dry-run setup-sql] %s", stmt.splitlines()[0])
                    continue
                try:
                    rest_client.query(stmt).result()
                except GoogleAPIError as exc:
                    return f"setup.sql failed: {exc}"

        for idx, setup_request in enumerate(fixture.setup_requests):
            if dry_run:
                logger.debug("[dry-run setup-req] %s %s", setup_request.method, setup_request.path)
                continue
            response = _issue_http_request(
                rest_http,
                setup_request,
                mapping={**base_mapping, **captured},
                source=f"{fixture.id} setup[#{idx}]",
            )
            if response.status_code >= _HTTP_SUCCESS_CEILING:
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
                body = _parse_response_json(response)
                if body is None:
                    return f"setup[#{idx}] capture failed: non-JSON response body"
                try:
                    captured.update(
                        {
                            name: str(resolve_dotted_path(body, dotted))
                            for name, dotted in setup_request.capture
                        }
                    )
                except (KeyError, IndexError) as exc:
                    return f"setup[#{idx}] capture failed: {exc}"

        if dry_run:
            logger.info("[dry-run] %s (would record)", fixture.id)
            return "ok"

        recorded_calls: list[dict[str, Any]] = []
        for call in fixture.request.calls:
            mapping = {**base_mapping, **captured}
            try:
                recorded = _record_one_call(grpc_channel, call, mapping=mapping)
            except grpc.RpcError as exc:
                code = exc.code() if hasattr(exc, "code") else grpc.StatusCode.UNKNOWN
                recorded = {
                    "method": call.method,
                    "status": code.name if code is not None else "UNKNOWN",
                    "error_message": exc.details() if hasattr(exc, "details") else str(exc),
                }
            recorded_calls.append(recorded)
            if call.capture and recorded.get("response"):
                captured.update(_apply_grpc_captures(call.capture, recorded["response"]))
            elif call.capture and recorded.get("responses"):
                first = recorded["responses"][0] if recorded["responses"] else None
                if first is not None:
                    captured.update(_apply_grpc_captures(call.capture, first))

        # Reverse-substitute recording-time identity strings into
        # placeholders so the baseline is portable across runners.
        # Longer matches go first so a partial replacement cannot
        # swallow the start of a longer one (e.g. project.dataset_id
        # must replace before the bare project name). Captured values
        # (stream names, session ids) follow the dataset bundle so a
        # captured stream name like ``projects/.../streams/<hex>``
        # that contains the dataset id still gets routed through the
        # dataset substitution first.
        reverse_substitutions: list[tuple[str, str]] = []
        # Captured values longest-first so e.g. a captured stream-name
        # that contains the recording-time dataset id reverses
        # cleanly via the captured placeholder (not the dataset one).
        captured_substitutions = sorted(
            ((value, f"${{{name}}}") for name, value in captured.items()),
            key=lambda pair: -len(pair[0]),
        )
        reverse_substitutions.extend(captured_substitutions)
        if dataset_fqdn is not None:
            reverse_substitutions.append((dataset_fqdn, "${DATASET}"))
            reverse_substitutions.append((dataset_fqdn.split(".", 1)[1], "${DATASET_ID}"))
        reverse_substitutions.append((project, "${PROJECT}"))
        for entry in recorded_calls:
            if "response" in entry:
                entry["response"] = _replace_in_value(entry["response"], reverse_substitutions)
            if "responses" in entry:
                entry["responses"] = [
                    _replace_in_value(r, reverse_substitutions) for r in entry["responses"]
                ]
            if "error_message" in entry and isinstance(entry["error_message"], str):
                msg_value: str = entry["error_message"]
                for needle, sub in reverse_substitutions:
                    msg_value = msg_value.replace(needle, sub)
                entry["error_message"] = msg_value

        # Mask volatile fields across every recorded message (after
        # placeholder substitution so the VOLATILE_PATHS dotted-path
        # walker still finds keys by their original names).
        for entry in recorded_calls:
            if "response" in entry and isinstance(entry["response"], dict):
                mask_volatile_fields(entry["response"], VOLATILE_PATHS)
            if "responses" in entry and isinstance(entry["responses"], list):
                for resp in entry["responses"]:
                    if isinstance(resp, dict):
                        mask_volatile_fields(resp, VOLATILE_PATHS)

        payload = {
            "fixture_version": FIXTURE_VERSION,
            "bigquery": {
                "project": project,
                "location": rest_client.location or "",
            },
            "calls": recorded_calls,
        }
        fixture.expected_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "[ok] %s (calls=%d)",
            fixture.id,
            len(recorded_calls),
        )
        return "ok"  # noqa: TRY300
    except Exception as exc:
        logger.exception("[fail] %s: recorder error", fixture.id)
        return f"recorder error: {type(exc).__name__}: {exc}"
    finally:
        if not dry_run:
            for proj_id, ds_id in reversed(rest_created_datasets):
                try:
                    rest_client.delete_dataset(
                        f"{proj_id}.{ds_id}",
                        delete_contents=True,
                        not_found_ok=True,
                    )
                except GoogleAPIError:  # pragma: no cover — cleanup-only
                    logger.warning("Failed to delete dataset %s.%s", proj_id, ds_id)
            if dataset_fqdn is not None:
                try:
                    rest_client.delete_dataset(
                        dataset_fqdn,
                        delete_contents=True,
                        not_found_ok=True,
                    )
                except GoogleAPIError:  # pragma: no cover — cleanup-only
                    logger.warning("Failed to delete dataset %s", dataset_fqdn)


def _record_one_call(
    channel: Any,
    call: GrpcCall,
    *,
    mapping: dict[str, str],
) -> dict[str, Any]:
    """Issue one gRPC call against real BQ and return the recorded outcome."""
    service_name, method_name = call.method.split(".", 1)
    service_path = GRPC_SERVICE_PATHS[service_name]
    rpc_path = f"{service_path}/{method_name}"

    if call.kind == "bidi_stream":
        expanded = [expand_placeholders_in_json(req, mapping) for req in call.requests]
        request_payloads = [serialize_request(call.method, _as_dict(req)) for req in expanded]
    else:
        expanded_one = (
            expand_placeholders_in_json(call.request, mapping) if call.request is not None else {}
        )
        request_payload = serialize_request(call.method, _as_dict(expanded_one))

    outcome: dict[str, Any] = {"method": call.method, "status": "OK"}
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
    return outcome


def _replace_in_value(value: object, substitutions: list[tuple[str, str]]) -> Any:
    """Walk a JSON-shaped value and apply each ``(needle, sub)`` to every string.

    Used by the recorder to reverse-substitute the recording-time
    project + dataset identifiers into ``${PROJECT}`` / ``${DATASET_ID}``
    / ``${DATASET}`` placeholders so the recorded baseline can be
    compared against the runner's emulator response (which carries a
    different project + dataset). ``substitutions`` is iterated in
    order — pass longer matches first so a partial match cannot
    swallow the start of a longer one.
    """
    if isinstance(value, str):
        out = value
        for needle, sub in substitutions:
            if needle:
                out = out.replace(needle, sub)
        return out
    if isinstance(value, list):
        return [_replace_in_value(item, substitutions) for item in value]
    if isinstance(value, dict):
        return {key: _replace_in_value(val, substitutions) for key, val in value.items()}
    return value


def _as_dict(value: object) -> dict[str, Any]:
    """Narrow a placeholder-expanded value to a dict."""
    if not isinstance(value, dict):
        msg = f"expected request payload to be a dict, got {type(value).__name__}"
        raise TypeError(msg)
    return value


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
    """Base placeholder mapping (PROJECT / DATASET / …)."""
    return {
        "DATASET": ctx.dataset,
        "PROJECT": ctx.project,
        "DATASET_ID": ctx.dataset_id,
        "PRINCIPAL": ctx.principal,
        "GROUP": ctx.group,
        "OTHER_PRINCIPAL": ctx.other_principal,
    }


def _issue_http_request(
    http: Any,
    request: HttpRequest,
    *,
    mapping: dict[str, str],
    source: str,
) -> Any:
    """Substitute placeholders and issue an HTTP request via the BQ AuthorizedSession."""
    try:
        path = expand_placeholders(request.path, mapping)
        body = (
            expand_placeholders_in_json(request.body, mapping) if request.body is not None else None
        )
        headers = {name: expand_placeholders(value, mapping) for name, value in request.headers}
    except (KeyError, ValueError) as exc:
        msg = f"{source}: placeholder expansion failed: {exc}"
        raise RuntimeError(msg) from exc
    url = f"{BQ_REST_BASE}{path}"
    return http.request(method=request.method, url=url, json=body, headers=headers or None)


def _parse_response_json(response: Any) -> object | None:
    """Best-effort parse of a response body into JSON."""
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


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
        help="Print the recording plan without issuing real-BQ calls.",
    )
    parser.add_argument(
        "--corpus-dir",
        default=str(GRPC_CORPUS_DIR),
        help="Override the corpus directory (default: tests/conformance/grpc_corpus).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


# proto_to_dict is exported by ``_grpc_corpus`` and re-imported here so the
# recorder + framework agree on the serialisation contract. Keeping the
# reference so ``ruff`` doesn't flag the import as unused.
_ = proto_to_dict

if __name__ == "__main__":  # pragma: no cover — script entrypoint
    raise SystemExit(main())
