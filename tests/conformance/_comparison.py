"""Type-aware row + schema comparison for the conformance tier.

The recorder produces ``expected.json`` files containing the result
rows BigQuery returned for a fixture's ``query.sql``. The runner
re-executes the same query against the in-process emulator and uses
this module to diff the two results with type-aware tolerance.

Tolerance contract (locked by ADR 0022):

* ``INT64``, ``BOOL``, ``BYTES``, ``STRING``, ``DATE``: exact equality.
* ``NUMERIC`` / ``BIGNUMERIC``: exact decimal equality (compared as
  :class:`decimal.Decimal` so trailing-zero serialisations are
  collapsed).
* ``FLOAT64``: ``math.isclose`` with ``rel_tol=1e-12`` and
  ``abs_tol=1e-15``. Two near-zero IEEE-754 doubles whose absolute
  difference falls within the abs_tol band pass; magnitudes above the
  abs_tol band pass when their relative difference is within rel_tol.
* ``TIME`` / ``DATETIME`` / ``TIMESTAMP``: ``abs(a - b) <= 1`` microsecond.
* ``GEOGRAPHY``: WKT string equality. Spheroidal-vs-planar coordinate
  drift is treated as a divergence by ADR 0022 and xfail'd at the
  fixture level — this helper does not attempt geometry normalisation.
* ``ARRAY``: ordered element-wise comparison using the element type's
  tolerance.
* ``STRUCT``: per-field comparison using each field's declared type.
* ``RANGE``: equality on the encoded ``{"start": …, "end": …}`` struct.
* ``INTERVAL``: stringified form equality (BigQuery serialises
  intervals as canonical ``YEAR TO SECOND`` strings).
* ``JSON``: parsed equality (both sides round-trip through
  ``json.loads`` so whitespace / key-order drift is ignored).

The helper is pure: it accepts two already-deserialised result
dictionaries (the schema describes each cell's type) and returns a
:class:`CompareReport` either flagging the first failing cell or
declaring the two results equivalent.

Error-shape parity (ADR 0022 §3 ``Error parity`` amendment, P3.a):

A fixture whose ``expected.json`` carries an ``error`` envelope is
expected to *fail* against the emulator with a matching error shape.
The runner catches the GoogleAPIError raised by the emulator,
extracts ``(reason, location, http_status, message)``, and calls
:func:`compare_error` to diff against the recorded values. ``reason``,
``location``, and ``http_status`` use exact equality; ``message`` is
matched with :func:`re.search` against the recorded
``message_pattern`` regex (the recorder substitutes the dataset name
back to ``${DATASET}`` and normalises line:column markers so the
pattern survives re-recordings against different projects).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
import math
import re
from typing import Any

FLOAT_REL_TOL = 1e-12
FLOAT_ABS_TOL = 1e-15
DATETIME_TOLERANCE = timedelta(microseconds=1)

#: WKT geometry-type keywords whose presence at the start of a STRING
#: value identifies it as WKT-shaped. Matched case-insensitively and
#: anchored at the start of the (stripped) value, followed by optional
#: whitespace and an opening paren. DuckDB-spatial's ``ST_AsText``
#: inserts a space between the keyword and the paren (``POINT (1 2)``)
#: where BigQuery omits it (``POINT(1 2)``); both shapes match the
#: pattern and route through :func:`_normalise_wkt`. See ADR 0022 §3
#: (WKT-shaped STRING amendment) and ADR 0023 §1.H closure.
_WKT_SHAPED_RE = re.compile(
    r"^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*\(",
    re.IGNORECASE,
)

#: JSON-shaped STRING detection. A STRING value whose stripped form
#: starts with ``{`` or ``[`` is a candidate for JSON parse-equal
#: tolerance. The check is intentionally cheap; the helper still
#: confirms the value parses cleanly before applying the tolerance.
#: See ADR 0022 §3 (JSON-shaped STRING amendment) and the
#: ``ST_AsGeoJSON`` closure note (scope-expansion 2026-05-17).
_JSON_SHAPED_OPENERS = ("{", "[")


@dataclass(slots=True)
class CompareReport:
    """The outcome of comparing two query results.

    ``ok`` is True iff every cell, schema field, and row count matches
    under the documented tolerances. When ``ok`` is False, ``reason``
    contains a human-readable explanation pointing at the first
    mismatch (path through the row + column + value).
    """

    ok: bool
    reason: str = ""
    diffs: list[str] = field(default_factory=list)


def compare_results(
    expected: dict[str, Any],
    actual_rows: list[dict[str, Any]],
    actual_schema: list[dict[str, Any]],
    actual_job_metadata: dict[str, Any] | None = None,
) -> CompareReport:
    """Diff the recorded ``expected`` payload against actual emulator output.

    ``expected`` is the loaded ``expected.json`` (full envelope with
    ``schema``, ``rows``, and metadata fields). ``actual_rows`` and
    ``actual_schema`` come from the emulator's query result and follow
    the same shape produced by the recorder.

    A schema mismatch terminates the comparison immediately: rows are
    not compared if the schemas disagree, since cell semantics depend
    on the declared column type.

    Response-object equivalence (P7.a, ADR 0022 §3 extension): when
    the recorded ``expected.json`` carries a ``job_metadata`` block,
    each key in that block is diffed against ``actual_job_metadata``.
    Keys absent from the recorded baseline are not asserted (so
    pre-P7.a fixtures keep their existing semantics untouched). The
    diff order is schema → rows → job_metadata — a row mismatch is
    surfaced before a job-metadata mismatch because the latter is
    diagnostic-only when rows already disagree.
    """
    schema_diff = _compare_schemas(expected["schema"], actual_schema)
    if schema_diff is not None:
        return CompareReport(ok=False, reason=schema_diff, diffs=[schema_diff])

    if len(expected["rows"]) != len(actual_rows):
        msg = f"row count differs: expected={len(expected['rows'])} actual={len(actual_rows)}"
        return CompareReport(ok=False, reason=msg, diffs=[msg])

    diffs: list[str] = []
    for row_index, (exp_row, act_row) in enumerate(zip(expected["rows"], actual_rows, strict=True)):
        for field_def in expected["schema"]:
            name = field_def["name"]
            cell_diff = _compare_cell(
                exp_row.get(name), act_row.get(name), field_def, path=f"rows[{row_index}].{name}"
            )
            if cell_diff is not None:
                diffs.append(cell_diff)
                # Limit per-fixture diff verbosity — three mismatches
                # is enough to debug; beyond that the test report
                # would be unwieldy.
                if len(diffs) >= 3:
                    break
        if len(diffs) >= 3:
            break

    if diffs:
        return CompareReport(ok=False, reason=diffs[0], diffs=diffs)

    # P7.a — response-object equivalence. Optional block; absent on
    # the ~878 pre-P7 fixtures so this loop is a no-op for them.
    expected_job_meta = expected.get("job_metadata")
    if isinstance(expected_job_meta, dict) and expected_job_meta:
        job_meta_diffs = _compare_job_metadata(expected_job_meta, actual_job_metadata or {})
        if job_meta_diffs:
            return CompareReport(
                ok=False,
                reason=job_meta_diffs[0],
                diffs=job_meta_diffs,
            )

    return CompareReport(ok=True)


def _compare_job_metadata(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[str]:
    """Diff the optional ``job_metadata`` block, key by key.

    Only the keys present in ``expected`` are asserted — a key absent
    from the recorded baseline is not required to be present on the
    actual side, so fixtures opt in by recording the keys they care
    about. The supported keys are documented in
    [`api-configuration-coverage-matrix`](../../docs/reference/api-configuration-coverage-matrix.md):

    - ``cache_hit`` (bool, exact equality)
    - ``statement_type`` (str, exact equality)
    - ``num_dml_affected_rows`` (int, exact equality)
    - ``ddl_operation_performed`` (str, exact equality)

    Unknown keys in the recorded baseline produce a diff so a typo
    fails loudly rather than silently passing.
    """
    diffs: list[str] = []
    supported_keys = {
        "cache_hit",
        "statement_type",
        "num_dml_affected_rows",
        "ddl_operation_performed",
    }
    unknown = set(expected) - supported_keys
    if unknown:
        diffs.append(
            f"job_metadata: unknown recorded keys {sorted(unknown)}; "
            f"supported keys: {sorted(supported_keys)}",
        )
        return diffs
    for key, exp_value in expected.items():
        if key not in actual:
            diffs.append(f"job_metadata.{key}: expected={exp_value!r} actual=<absent>")
            continue
        act_value = actual[key]
        if exp_value != act_value:
            diffs.append(
                f"job_metadata.{key}: expected={exp_value!r} actual={act_value!r}",
            )
    return diffs


def _compare_schemas(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> str | None:
    """Compare two BigQuery-shaped schema descriptors.

    Returns a human-readable mismatch message or ``None`` when the two
    schemas match field-for-field on ``name``, ``type``, ``mode``, and
    (for STRUCT / RECORD types) nested ``fields``.
    """
    if len(expected) != len(actual):
        return f"schema length differs: expected={len(expected)} actual={len(actual)}"
    for index, (exp_field, act_field) in enumerate(zip(expected, actual, strict=True)):
        if exp_field.get("name") != act_field.get("name"):
            return (
                f"schema[{index}].name: expected={exp_field.get('name')!r} "
                f"actual={act_field.get('name')!r}"
            )
        if _normalise_type(exp_field.get("type", "")) != _normalise_type(act_field.get("type", "")):
            return (
                f"schema[{index}].type ({exp_field.get('name')}): "
                f"expected={exp_field.get('type')!r} "
                f"actual={act_field.get('type')!r}"
            )
        # BigQuery omits ``mode`` for the default NULLABLE — normalise
        # before comparing so a recorded NULLABLE and an emulator-
        # returned omitted mode don't trigger a spurious diff.
        if _normalise_mode(exp_field.get("mode")) != _normalise_mode(act_field.get("mode")):
            return (
                f"schema[{index}].mode ({exp_field.get('name')}): "
                f"expected={exp_field.get('mode')!r} "
                f"actual={act_field.get('mode')!r}"
            )
        exp_nested = exp_field.get("fields") or []
        act_nested = act_field.get("fields") or []
        if exp_nested or act_nested:
            nested = _compare_schemas(exp_nested, act_nested)
            if nested is not None:
                return f"schema[{index}].fields ({exp_field.get('name')}): {nested}"
    return None


def _normalise_type(t: str) -> str:
    """Collapse BigQuery type aliases to a canonical form.

    BigQuery accepts several spellings (``INTEGER`` ⇆ ``INT64``,
    ``FLOAT`` ⇆ ``FLOAT64``, ``BOOLEAN`` ⇆ ``BOOL``, ``RECORD`` ⇆
    ``STRUCT``) and the catalog round-trip can normalise these
    independently between BQ and the emulator. We compare on the
    canonical form to avoid false mismatches.
    """
    aliases = {
        "INTEGER": "INT64",
        "FLOAT": "FLOAT64",
        "BOOLEAN": "BOOL",
        "RECORD": "STRUCT",
    }
    return aliases.get(t.upper(), t.upper())


def _normalise_mode(mode: str | None) -> str:
    """Map an absent ``mode`` field to the BigQuery default."""
    if mode is None or mode == "":
        return "NULLABLE"
    return mode.upper()


def _compare_cell(
    expected: Any,
    actual: Any,
    field_def: dict[str, Any],
    *,
    path: str,
) -> str | None:
    """Compare one cell value with type-aware tolerance.

    ``field_def`` is the schema field for the column (used for type
    + mode + nested fields). Returns ``None`` on a match or a
    human-readable mismatch string.
    """
    mode = _normalise_mode(field_def.get("mode"))
    field_type = _normalise_type(field_def.get("type", ""))

    # REPEATED columns are arrays of the underlying type. Real BigQuery
    # returns ``[]`` for empty arrays, while the emulator may return
    # ``None``; normalise both before length comparison.
    if mode == "REPEATED":
        return _compare_array(expected, actual, field_def, path=path)

    if expected is None and actual is None:
        return None
    if expected is None or actual is None:
        return f"{path}: expected={expected!r} actual={actual!r} (NULL mismatch)"

    if field_type == "STRUCT":
        return _compare_struct(expected, actual, field_def, path=path)
    return _compare_scalar(expected, actual, field_type, path=path)


def _compare_array(
    expected: Any,
    actual: Any,
    field_def: dict[str, Any],
    *,
    path: str,
) -> str | None:
    expected_list = expected if expected is not None else []
    actual_list = actual if actual is not None else []
    if not isinstance(expected_list, list) or not isinstance(actual_list, list):
        return f"{path}: expected={expected!r} actual={actual!r} (REPEATED type mismatch)"
    if len(expected_list) != len(actual_list):
        return (
            f"{path}: array length differs expected={len(expected_list)} actual={len(actual_list)}"
        )
    element_def = {k: v for k, v in field_def.items() if k != "mode"}
    element_def["mode"] = "NULLABLE"
    for index, (exp_elem, act_elem) in enumerate(zip(expected_list, actual_list, strict=True)):
        diff = _compare_cell(exp_elem, act_elem, element_def, path=f"{path}[{index}]")
        if diff is not None:
            return diff
    return None


def _compare_struct(
    expected: Any,
    actual: Any,
    field_def: dict[str, Any],
    *,
    path: str,
) -> str | None:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return f"{path}: expected={expected!r} actual={actual!r} (STRUCT type mismatch)"
    fields = field_def.get("fields") or []
    for nested in fields:
        name = nested["name"]
        diff = _compare_cell(expected.get(name), actual.get(name), nested, path=f"{path}.{name}")
        if diff is not None:
            return diff
    return None


def _compare_scalar(
    expected: Any,
    actual: Any,
    field_type: str,
    *,
    path: str,
) -> str | None:
    if field_type == "FLOAT64":
        return _compare_float(expected, actual, path=path)
    if field_type in {"NUMERIC", "BIGNUMERIC"}:
        return _compare_decimal(expected, actual, path=path)
    if field_type == "TIMESTAMP":
        return _compare_timestamp(expected, actual, path=path)
    if field_type == "DATETIME":
        return _compare_datetime(expected, actual, path=path)
    if field_type == "TIME":
        return _compare_time(expected, actual, path=path)
    if field_type == "DATE":
        return _compare_date(expected, actual, path=path)
    if field_type == "BYTES":
        return _compare_bytes(expected, actual, path=path)
    if field_type == "JSON":
        return _compare_json(expected, actual, path=path)
    if field_type == "GEOGRAPHY":
        return _compare_geography(expected, actual, path=path)
    if field_type == "STRING" and _is_wkt_shaped(expected) and _is_wkt_shaped(actual):
        # ADR 0022 §3 WKT-shaped STRING sub-rule (ADR 0023 §1.H
        # closure): DuckDB's ``ST_AsText`` inserts a space between the
        # geometry-type keyword and the opening paren; BigQuery omits
        # it. Both sides match the WKT shape, so apply the same
        # whitespace + capitalisation normalisation the GEOGRAPHY rule
        # uses.
        return _compare_geography(expected, actual, path=path)
    if field_type == "STRING" and _is_json_shaped(expected) and _is_json_shaped(actual):
        # ADR 0022 §3 JSON-shaped STRING sub-rule (out-of-scope
        # ST_AsGeoJSON closure, 2026-05-17): DuckDB-spatial's
        # ``ST_AsGeoJSON`` emits JSON with the opposite key order and
        # float-typed whole-number coordinates relative to BigQuery's
        # output. The values are semantically equivalent JSON objects.
        # Parse both sides through ``json.loads`` and compare with
        # Python's unordered ``==`` (which treats ``3`` and ``3.0`` as
        # equal). Falls back to exact equality if either side fails to
        # parse — so a genuinely-malformed JSON string still surfaces
        # as a mismatch.
        return _compare_json_shaped_string(expected, actual, path=path)
    # INT64, BOOL, STRING, INTERVAL, RANGE — exact equality on the
    # serialised form. RANGE round-trips as the BigQuery wire shape
    # ``{"start": "...", "end": "..."}``; INTERVAL as a canonical
    # ``YEAR TO SECOND`` string.
    if expected == actual:
        return None
    return f"{path}: expected={expected!r} actual={actual!r}"


def _is_wkt_shaped(value: Any) -> bool:
    """Return True iff ``value`` is a STRING starting with a WKT keyword.

    The regex is anchored and case-insensitive so spurious STRING
    values (URLs, JSON, ordinary text) never trigger the WKT-shaped
    normalisation branch. Leading whitespace is stripped before the
    pattern check because BigQuery's recorded WKT values occasionally
    carry a leading newline from the wire encoder.
    """
    if not isinstance(value, str):
        return False
    return _WKT_SHAPED_RE.match(value.lstrip()) is not None


def _is_json_shaped(value: Any) -> bool:
    """Return True iff ``value`` is a STRING whose stripped form opens with ``{`` or ``[``.

    Cheap shape check used only as a gating signal — the actual
    parse-equal comparison validates the JSON content via
    ``json.loads`` before applying the tolerance. A value that opens
    with ``{``/``[`` but fails to parse falls back to exact equality
    so genuine malformed-JSON mismatches still surface.
    """
    if not isinstance(value, str):
        return False
    stripped = value.lstrip()
    if not stripped:
        return False
    return stripped[0] in _JSON_SHAPED_OPENERS


def _compare_json_shaped_string(expected: str, actual: str, *, path: str) -> str | None:
    """Compare two JSON-shaped STRING values via parse-equal semantics.

    Both sides are stripped and parsed through ``json.loads``. The
    parsed objects are compared via :func:`_objects_equal_with_float_tolerance`
    so dict key order, ``int``/``float`` numeric equivalence
    (BigQuery's ``ST_AsGeoJSON`` returns integer coords like
    ``[3, 4]`` where DuckDB-spatial emits ``[3.0, 4.0]`` — semantically
    the same point), AND FLOAT64 ULP drift on coordinate values (the
    geodesic-interpolation maths use libm functions that differ by 1-2
    ULPs from BigQuery's S2 / proprietary implementation) are all
    tolerated. The float tolerance is the same as the native
    FLOAT64 comparison (``rel_tol=1e-12, abs_tol=1e-15``) so a
    coordinate difference that would fail a native FLOAT64 column
    comparison still surfaces here. If either side fails to parse,
    falls back to exact equality so a genuine malformed-JSON string
    still surfaces.
    """
    try:
        exp_obj = json.loads(expected.strip())
        act_obj = json.loads(actual.strip())
    except json.JSONDecodeError:
        if expected == actual:
            return None
        return f"{path}: expected={expected!r} actual={actual!r}"
    if _objects_equal_with_float_tolerance(exp_obj, act_obj):
        return None
    return f"{path}: json-shaped string mismatch expected={expected!r} actual={actual!r}"


def _objects_equal_with_float_tolerance(expected: Any, actual: Any) -> bool:
    """Deep-compare two parsed-JSON values with ULP tolerance for floats.

    Used by :func:`_compare_json_shaped_string` so GeoJSON coordinate
    values that differ in the last 1-2 ULPs (due to libm vs S2 /
    BigQuery math-library implementation drift on ``asin`` / ``atan2`` /
    ``sin`` / ``cos``) are treated as equal at the same tolerance the
    native FLOAT64 column comparator applies. The walk is structurally
    strict — dict keys must match, list lengths must match, and
    non-float scalars are compared with ``==``.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        if expected.keys() != actual.keys():
            return False
        return all(_objects_equal_with_float_tolerance(expected[k], actual[k]) for k in expected)
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        if len(expected) != len(actual):
            return False
        return all(
            _objects_equal_with_float_tolerance(e, a) for e, a in zip(expected, actual, strict=True)
        )
    # ``bool`` is a subclass of ``int`` in Python — ``True == 1`` is
    # True, ``isinstance(True, int)`` is True. We don't want a ``true``
    # JSON literal silently comparing equal to a ``1``; if either side
    # is a bool, BOTH must be bools (and equal).
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        # Treat ``int`` and ``float`` numerically — BigQuery emits whole
        # numbers as integers; DuckDB-spatial as floats. ``math.isclose``
        # absorbs both the int/float widening AND the ULP drift on
        # genuine floats.
        exp_f = float(expected)
        act_f = float(actual)
        if math.isnan(exp_f) and math.isnan(act_f):
            return True
        return math.isclose(exp_f, act_f, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL)
    return expected == actual


def _compare_float(expected: Any, actual: Any, *, path: str) -> str | None:
    try:
        exp_value = float(expected)
        act_value = float(actual)
    except (TypeError, ValueError):
        return f"{path}: non-float value expected={expected!r} actual={actual!r}"
    if math.isnan(exp_value) and math.isnan(act_value):
        return None
    if math.isinf(exp_value) and math.isinf(act_value) and ((exp_value > 0) == (act_value > 0)):
        return None
    if math.isclose(exp_value, act_value, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL):
        return None
    return (
        f"{path}: float mismatch expected={exp_value!r} actual={act_value!r} "
        f"(rel_tol={FLOAT_REL_TOL}, abs_tol={FLOAT_ABS_TOL})"
    )


def _compare_decimal(expected: Any, actual: Any, *, path: str) -> str | None:
    try:
        exp_value = Decimal(str(expected))
        act_value = Decimal(str(actual))
    except Exception:  # noqa: BLE001
        return f"{path}: non-decimal value expected={expected!r} actual={actual!r}"
    if exp_value == act_value:
        return None
    return f"{path}: decimal mismatch expected={exp_value!r} actual={act_value!r}"


def _compare_timestamp(expected: Any, actual: Any, *, path: str) -> str | None:
    exp_dt = _parse_timestamp(expected)
    act_dt = _parse_timestamp(actual)
    if exp_dt is None or act_dt is None:
        return f"{path}: timestamp parse failed expected={expected!r} actual={actual!r}"
    if abs(exp_dt - act_dt) <= DATETIME_TOLERANCE:
        return None
    return f"{path}: timestamp mismatch expected={exp_dt!r} actual={act_dt!r}"


def _compare_datetime(expected: Any, actual: Any, *, path: str) -> str | None:
    exp_dt = _parse_datetime(expected)
    act_dt = _parse_datetime(actual)
    if exp_dt is None or act_dt is None:
        return f"{path}: datetime parse failed expected={expected!r} actual={actual!r}"
    if abs(exp_dt - act_dt) <= DATETIME_TOLERANCE:
        return None
    return f"{path}: datetime mismatch expected={exp_dt!r} actual={act_dt!r}"


def _compare_time(expected: Any, actual: Any, *, path: str) -> str | None:
    exp_t = _parse_time(expected)
    act_t = _parse_time(actual)
    if exp_t is None or act_t is None:
        return f"{path}: time parse failed expected={expected!r} actual={actual!r}"
    delta = abs(_time_to_microseconds(exp_t) - _time_to_microseconds(act_t))
    if delta <= 1:
        return None
    return f"{path}: time mismatch expected={exp_t!r} actual={act_t!r}"


def _compare_date(expected: Any, actual: Any, *, path: str) -> str | None:
    exp_d = _parse_date(expected)
    act_d = _parse_date(actual)
    if exp_d is None or act_d is None:
        return f"{path}: date parse failed expected={expected!r} actual={actual!r}"
    if exp_d == act_d:
        return None
    return f"{path}: date mismatch expected={exp_d!r} actual={act_d!r}"


def _compare_bytes(expected: Any, actual: Any, *, path: str) -> str | None:
    exp_b = _coerce_bytes(expected)
    act_b = _coerce_bytes(actual)
    if exp_b == act_b:
        return None
    return f"{path}: bytes mismatch expected={exp_b!r} actual={act_b!r}"


def _compare_json(expected: Any, actual: Any, *, path: str) -> str | None:
    exp_json = _parse_json(expected)
    act_json = _parse_json(actual)
    if exp_json == act_json:
        return None
    return f"{path}: json mismatch expected={expected!r} actual={actual!r}"


def _compare_geography(expected: Any, actual: Any, *, path: str) -> str | None:
    # WKT strings differ only in whitespace / capitalisation between
    # BigQuery (``POINT(1 2)``) and DuckDB's spatial output. Normalise
    # whitespace before comparing; coordinate-order differences (e.g.
    # ``MULTIPOINT`` element reordering) are treated as divergences
    # and handled at the fixture level via xfail.
    exp_norm = _normalise_wkt(expected)
    act_norm = _normalise_wkt(actual)
    if exp_norm == act_norm:
        return None
    return f"{path}: geography mismatch expected={expected!r} actual={actual!r}"


def _normalise_wkt(value: Any) -> str:
    if not isinstance(value, str):
        return repr(value)
    collapsed = " ".join(value.split())
    return (
        collapsed.upper()
        .replace(" ,", ",")
        .replace(", ", ",")
        .replace(" (", "(")
        .replace("( ", "(")
        .replace(" )", ")")
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.replace("Z", "+00:00").replace("T", " ")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _parse_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _time_to_microseconds(t: time) -> int:
    return t.hour * 3_600_000_000 + t.minute * 60_000_000 + t.second * 1_000_000 + t.microsecond


def _coerce_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        try:
            return base64.b64decode(value)
        except Exception:  # noqa: BLE001
            return value.encode("utf-8")
    return repr(value).encode("utf-8")


def _parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def extract_actual_error(exc: Exception) -> dict[str, Any]:
    """Normalise a :class:`google.api_core.exceptions.GoogleAPIError` for diffing.

    Returns the recorder-shaped dict the conformance runner passes to
    :func:`compare_error`. Reads BigQuery's ``ErrorProto.errors[]``
    payload via the exception's ``errors`` attribute when present and
    falls back to the exception's ``message`` / ``code`` attributes
    when the structured payload is absent. Non-API-call exceptions
    surface their ``str(exc)`` in ``message`` and ``None`` everywhere
    else; the recorder will not have written that fixture shape, so
    the comparison helper will surface the mismatch cleanly.
    """
    reason: str | None = None
    location: str | None = None
    http_status: int | None = None
    message: str = str(exc)

    errors_attr = getattr(exc, "errors", None)
    if errors_attr:
        first = errors_attr[0]
        if isinstance(first, dict):
            reason = first.get("reason")
            location = first.get("location")
            # Prefer the structured per-error message — that's what
            # real BigQuery surfaces to the client try/except path,
            # not the top-level exception summary.
            err_message = first.get("message")
            if err_message:
                message = err_message
    else:
        # No structured ``ErrorProto.errors[]`` (rare for BQ but
        # possible for non-call exceptions). Fall back to the
        # ``GoogleAPICallError.message`` attribute when present.
        message_attr = getattr(exc, "message", None)
        if message_attr:
            message = str(message_attr)

    code_attr = getattr(exc, "code", None)
    if isinstance(code_attr, int):
        http_status = code_attr

    return {
        "reason": reason,
        "location": location,
        "http_status": http_status,
        "message": message,
    }


def compare_error(expected: dict[str, Any], actual: dict[str, Any]) -> CompareReport:
    """Diff a recorded BigQuery error envelope against the emulator's error.

    ``expected`` is the recorded ``error`` sub-object from
    ``expected.json``: ``{reason, location, http_status,
    message_pattern}``. ``actual`` is the normalised error shape from
    :func:`extract_actual_error`.

    ``reason``, ``location``, and ``http_status`` use exact equality
    (BigQuery's ``ErrorProto.reason`` is a closed enum; ``location``
    points at the structural element that failed validation;
    ``http_status`` is the HTTP code the client try/except keys on).
    ``message_pattern`` is a Python regex matched against
    ``actual["message"]`` via :func:`re.search`. The recorder writes
    the pattern with dataset-name and line:column wildcards already
    expanded so the same pattern survives re-recordings against
    different projects. See ADR 0022 §3 ``Error parity``.
    """
    diffs: list[str] = [
        f"error.{field_name}: expected={expected.get(field_name)!r} "
        f"actual={actual.get(field_name)!r}"
        for field_name in ("reason", "location", "http_status")
        if expected.get(field_name) != actual.get(field_name)
    ]
    pattern = expected.get("message_pattern")
    actual_message = actual.get("message") or ""
    if pattern is None:
        diffs.append("error.message_pattern: missing on recorded expected.json")
    else:
        try:
            compiled = re.compile(pattern, re.DOTALL)
        except re.error as exc:
            diffs.append(f"error.message_pattern: invalid regex {pattern!r}: {exc}")
        else:
            if compiled.search(actual_message) is None:
                diffs.append(
                    f"error.message: pattern {pattern!r} did not match actual "
                    f"message {actual_message!r}"
                )
    if diffs:
        return CompareReport(ok=False, reason=diffs[0], diffs=diffs)
    return CompareReport(ok=True)


__all__ = [
    "DATETIME_TOLERANCE",
    "FLOAT_ABS_TOL",
    "FLOAT_REL_TOL",
    "CompareReport",
    "compare_error",
    "compare_results",
    "extract_actual_error",
]
