"""gRPC-shape comparator for the P3.d conformance corpus.

Each fixture's recorded baseline (``expected_response.json``) is a
sequence of ``GrpcExpectedCall`` entries, one per gRPC call in the
fixture's ``request.json``. The comparator diffs each entry against
the runner's actual call outcome using **structural subset**
semantics — every key in the recorded baseline must be present in
the actual response (unless the recorded value is the ``WILDCARD``
sentinel ``"<*>"``), but extra emulator-side keys are tolerated.

The shape contract:

- ``status`` — matched exactly. Real BigQuery's status name maps
  directly to ``grpc.StatusCode`` (``OK`` / ``NOT_FOUND`` /
  ``INVALID_ARGUMENT`` / ``ALREADY_EXISTS`` / ``OUT_OF_RANGE`` /
  ``FAILED_PRECONDITION``).
- ``error_message`` — when present in the recorded baseline (only
  meaningful when ``status != OK``), the actual error_message must
  *contain* the recorded text. Real BQ's error wording varies
  slightly between recordings (timestamps, generated IDs); the
  containment check is the loosest reasonable assertion that still
  catches structural drift in the error envelope.
- ``response`` (unary call) — single proto-as-JSON dict diffed via
  :func:`_diff_message`.
- ``responses`` (server / bidi stream) — recorded list and actual
  list must have the same length, and each element is diffed
  recursively.

The same WILDCARD + structural-subset rules used by P2.f's HTTP
corpus apply here: wildcards live at leaves; the comparator walks
dicts/lists recursively; missing keys in the recorded baseline are
tolerated by definition.

The G3 Avro fixtures add a **three-layer** comparison on top of the
structural-subset diff:

1. Proto-envelope structural subset (the existing path above).
2. Avro schema canonical equality — both the recorded and emulator
   schemas are parsed with :func:`fastavro.parse_schema` and the
   normalised dicts compared for equality.
3. Decoded-row equality — the emulator's
   ``serialized_binary_rows`` bytes are decoded via
   :func:`fastavro.schemaless_reader` against the schema, and the
   resulting Python row list is compared against the recorded
   ``decoded_rows`` slice.

The byte-level Avro payload is intentionally NOT compared: Avro
encoders may legitimately emit different binary representations for
the same logical rows (varint padding, optional union ordering,
etc.). The decoded values ARE compared, after standard
FLOAT64 / numeric-tolerance rules (ADR 0022 §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
from typing import Any

from tests.conformance._grpc_corpus import WILDCARD


@dataclass(slots=True)
class GrpcCompareReport:
    """The outcome of comparing recorded and actual gRPC call sequences."""

    ok: bool
    diffs: list[str] = field(default_factory=list)


def compare_grpc_calls(
    *,
    expected: list,
    actual: list,
) -> GrpcCompareReport:
    """Diff a recorded call sequence against an actual sequence.

    ``expected`` is a list of :class:`GrpcExpectedCall`; ``actual``
    is a list of homogeneous dicts with the same keys
    (``method``, ``status``, optionally ``response`` /
    ``responses`` / ``error_message``) — the runner assembles those
    directly from the gRPC channel's outputs.

    Returns a :class:`GrpcCompareReport` listing every diff found.
    """
    diffs: list[str] = []

    if len(expected) != len(actual):
        diffs.append(f"call_count: expected={len(expected)} actual={len(actual)}")
        return GrpcCompareReport(ok=False, diffs=diffs)

    for idx, (exp, act) in enumerate(zip(expected, actual, strict=True)):
        if exp.method != act.get("method"):
            diffs.append(
                f"calls[{idx}].method: expected={exp.method!r} actual={act.get('method')!r}"
            )
            continue
        if exp.status != act.get("status"):
            diffs.append(
                f"calls[{idx}].status: expected={exp.status!r} actual={act.get('status')!r}"
            )

        if exp.error_message is not None:
            actual_msg = act.get("error_message") or ""
            if exp.error_message not in actual_msg:
                diffs.append(
                    f"calls[{idx}].error_message: expected to contain "
                    f"{exp.error_message!r}, actual={actual_msg!r}"
                )

        if exp.response is not None:
            actual_response = act.get("response")
            if actual_response is None:
                diffs.append(
                    f"calls[{idx}].response: expected={_describe(exp.response)} actual=<absent>"
                )
            else:
                _diff_message(
                    exp.response,
                    actual_response,
                    path=f"calls[{idx}].response",
                    diffs=diffs,
                )

        if exp.responses is not None:
            actual_responses = act.get("responses")
            if not isinstance(actual_responses, list):
                diffs.append(
                    f"calls[{idx}].responses: expected list of {len(exp.responses)} actual=<absent>"
                )
            elif len(exp.responses) != len(actual_responses):
                diffs.append(
                    f"calls[{idx}].responses: list length mismatch "
                    f"expected={len(exp.responses)} actual={len(actual_responses)}"
                )
            else:
                for mid, (exp_msg, act_msg) in enumerate(
                    zip(exp.responses, actual_responses, strict=True)
                ):
                    _diff_message(
                        exp_msg,
                        act_msg,
                        path=f"calls[{idx}].responses[{mid}]",
                        diffs=diffs,
                    )

    return GrpcCompareReport(ok=not diffs, diffs=diffs)


def _diff_message(
    expected: object,
    actual: object,
    *,
    path: str,
    diffs: list[str],
) -> None:
    """Recursive structural-subset diff with WILDCARD support.

    Mirrors :func:`tests.conformance._http_comparison._diff_body`.
    Duplicated rather than re-used because the two corpora may diverge
    on field-naming conventions later (gRPC is snake_case across the
    board, REST mixes camelCase and snake_case depending on the
    surface).
    """
    if expected == WILDCARD:
        return

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            diffs.append(f"{path}: expected object, actual={_describe(actual)}")
            return
        for key, expected_value in expected.items():
            sub_path = f"{path}.{key}"
            if expected_value == WILDCARD:
                # WILDCARD accepts absent OR present. Useful when the
                # emulator surfaces a field only on some inputs (e.g.
                # arrow_schema only on the first ReadRowsResponse).
                continue
            if key not in actual:
                diffs.append(f"{sub_path}: expected={_describe(expected_value)} actual=<absent>")
                continue
            _diff_message(expected_value, actual[key], path=sub_path, diffs=diffs)
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            diffs.append(f"{path}: expected list, actual={_describe(actual)}")
            return
        if len(expected) != len(actual):
            diffs.append(
                f"{path}: list length mismatch expected={len(expected)} actual={len(actual)}"
            )
            return
        for idx, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=True)):
            _diff_message(expected_item, actual_item, path=f"{path}[{idx}]", diffs=diffs)
        return

    if expected != actual:
        diffs.append(f"{path}: expected={expected!r} actual={actual!r}")


def _describe(value: object) -> str:
    """Compact, type-aware repr for diff messages."""
    if isinstance(value, dict):
        return f"object(keys={sorted(value.keys())!r})"
    if isinstance(value, list):
        return f"list(len={len(value)})"
    return repr(value)


def mask_volatile_fields(value: object, paths: tuple[str, ...]) -> object:
    """Replace the values at every dotted ``paths`` entry with ``WILDCARD``.

    Used by the recorder to scrub server-generated opaque values from
    the recorded baseline. ``[]`` matches every element of a list at
    that point (mirrors the HTTP corpus's convention).
    """
    for path in paths:
        _apply_mask(value, path.split("."))
    return value


def _apply_mask(cursor: object, segments: list[str]) -> None:
    """Recursive helper for :func:`mask_volatile_fields`."""
    if not segments:
        return
    head, *tail = segments

    list_each = False
    if head.endswith("[]"):
        list_each = True
        head = head[:-2]

    if isinstance(cursor, dict):
        if head not in cursor:
            return
        nested = cursor[head]
        if list_each:
            if not isinstance(nested, list):
                return
            for item in nested:
                if not tail:
                    continue
                _apply_mask(item, tail)
            return
        if not tail:
            cursor[head] = WILDCARD
            return
        _apply_mask(nested, tail)


def compare_avro_schema(
    *,
    recorded_schema_json: str,
    actual_schema_json: str,
) -> list[str]:
    """Diff two Avro JSON schemas for canonical equality.

    Both schemas are parsed via :func:`fastavro.parse_schema` to
    normalise the dict shape (key ordering, default values, namespace
    propagation) and compared by dict equality. Returns a list of
    diff strings — empty when the schemas are canonically equal.

    Raises ``ValueError`` (surfaced as a diff entry, NOT propagated)
    if either schema fails to parse.
    """
    import fastavro

    diffs: list[str] = []
    try:
        recorded_parsed = fastavro.parse_schema(json.loads(recorded_schema_json))
    except (ValueError, KeyError, TypeError) as exc:
        diffs.append(f"avro_schema: recorded schema is not valid Avro: {exc}")
        return diffs
    try:
        actual_parsed = fastavro.parse_schema(json.loads(actual_schema_json))
    except (ValueError, KeyError, TypeError) as exc:
        diffs.append(f"avro_schema: emulator schema is not valid Avro: {exc}")
        return diffs

    if recorded_parsed != actual_parsed:
        diffs.append(
            "avro_schema: canonical-parse mismatch\n"
            f"  recorded: {recorded_parsed}\n"
            f"  actual:   {actual_parsed}"
        )
    return diffs


def decode_and_compare_avro_rows(
    *,
    schema_json: str,
    actual_bytes: bytes,
    expected_decoded_rows: list[dict],
) -> list[str]:
    """Decode emulator-emitted naked rows; diff vs the recorded list.

    Bytes are NOT compared (encoder freedom in varint / union order).
    Decoded values ARE compared, with these tolerances:

    * floats compared with ``math.isclose`` (rel_tol=1e-9, abs_tol=1e-12)
      per ADR 0022 §3.
    * Decimals compared by equality.
    * dicts / lists compared structurally.

    Returns a list of diff strings; empty when every row equals.

    Errors that abort the decode (truncated bytes, schema mismatch)
    surface as a single diff string identifying the failure mode
    rather than raising — the comparator's contract is
    "list all problems," not "fail at the first."
    """
    import fastavro

    diffs: list[str] = []
    try:
        parsed_schema = fastavro.parse_schema(json.loads(schema_json))
    except (ValueError, KeyError, TypeError) as exc:
        diffs.append(f"avro_rows: schema parse failed: {exc}")
        return diffs

    decoded_rows: list[dict] = []
    reader = io.BytesIO(actual_bytes)
    for idx in range(len(expected_decoded_rows)):
        try:
            row = fastavro.schemaless_reader(reader, parsed_schema)
        except Exception as exc:  # noqa: BLE001 — surface any decode failure
            diffs.append(
                f"avro_rows[{idx}]: decode failed at byte offset {reader.tell()}: "
                f"{type(exc).__name__}: {exc}"
            )
            return diffs
        decoded_rows.append(row)

    if len(decoded_rows) != len(expected_decoded_rows):
        diffs.append(
            f"avro_rows: row count mismatch "
            f"expected={len(expected_decoded_rows)} actual={len(decoded_rows)}"
        )
        return diffs

    for idx, (exp, act) in enumerate(zip(expected_decoded_rows, decoded_rows, strict=True)):
        row_diffs: list[str] = []
        _diff_avro_value(exp, act, path=f"avro_rows[{idx}]", diffs=row_diffs)
        diffs.extend(row_diffs)
    return diffs


def _diff_avro_value(
    expected: Any,
    actual: Any,
    *,
    path: str,
    diffs: list[str],
) -> None:
    """Recursive value-diff with float tolerance + dict/list descent."""
    import math

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            diffs.append(f"{path}: expected dict, actual={type(actual).__name__}")
            return
        for key in expected:
            if key not in actual:
                diffs.append(f"{path}.{key}: absent in actual row")
                continue
            _diff_avro_value(expected[key], actual[key], path=f"{path}.{key}", diffs=diffs)
        # Tolerate emulator-side extra keys (matches the structural-subset
        # philosophy of the proto-envelope comparator).
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            diffs.append(f"{path}: expected list, actual={type(actual).__name__}")
            return
        if len(expected) != len(actual):
            diffs.append(
                f"{path}: list length mismatch expected={len(expected)} actual={len(actual)}"
            )
            return
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            _diff_avro_value(e, a, path=f"{path}[{i}]", diffs=diffs)
        return

    if isinstance(expected, float) and isinstance(actual, float):
        if not math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-12):
            diffs.append(f"{path}: expected={expected!r} actual={actual!r}")
        return

    if expected != actual:
        diffs.append(f"{path}: expected={expected!r} actual={actual!r}")


__all__ = [
    "GrpcCompareReport",
    "compare_avro_schema",
    "compare_grpc_calls",
    "decode_and_compare_avro_rows",
    "mask_volatile_fields",
]
