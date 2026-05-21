"""HTTP-shape comparator for the P2.f conformance corpus.

The HTTP corpus diffs a recorded ``expected_response.json`` against
the emulator's runtime response. Comparison rules:

- ``http_status`` is matched exactly.
- ``headers`` are subset-matched: only the headers explicitly listed
  in the recorded baseline are checked. BigQuery adds opaque headers
  (e.g. ``x-cloud-trace-id``) that drift between recordings and
  shouldn't be diffed. Header names are normalised to lowercase before
  comparison since HTTP semantics treat them as case-insensitive.
- ``body`` is matched with **structural subset** semantics:
   - Recorded ``WILDCARD`` (``"<*>"``) at a leaf accepts any value at
     that key — including the key being absent in the actual; this
     covers BigQuery responses that omit fields the emulator surfaces
     and vice versa.
   - Recorded dict keys must exist in the actual dict (unless the
     recorded value is ``WILDCARD``); extra keys in the actual dict
     are tolerated.
   - Recorded lists must match the actual list element-wise. List
     length must match. Each element is diffed recursively.
   - Recorded scalar values must equal the actual scalar value via
     ``==``.

The recorded body is the **partial schema**: the things the emulator
must surface. Extra keys in the emulator's response are intentional
slack — BigQuery's wire format adds fields over time, and pinning every
key would break the corpus on every BQ minor release.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.conformance._http_corpus import WILDCARD


@dataclass(slots=True)
class HttpCompareReport:
    """The outcome of comparing two HTTP responses."""

    ok: bool
    diffs: list[str] = field(default_factory=list)


def compare_http_response(
    *,
    expected_status: int,
    expected_body: object,
    expected_headers: tuple[tuple[str, str], ...],
    actual_status: int,
    actual_body: object,
    actual_headers: dict[str, str],
) -> HttpCompareReport:
    """Diff a recorded baseline against an actual emulator response.

    Returns a :class:`HttpCompareReport` listing every diff found.
    The runner reports all diffs (not just the first) so an operator
    can triage multiple shape regressions in one pass.
    """
    diffs: list[str] = []

    if expected_status != actual_status:
        diffs.append(f"http_status: expected={expected_status} actual={actual_status}")

    _diff_body(expected_body, actual_body, path="body", diffs=diffs)

    if expected_headers:
        actual_norm = {name.lower(): value for name, value in actual_headers.items()}
        for name, value in expected_headers:
            key = name.lower()
            if key not in actual_norm:
                diffs.append(f"headers.{name}: expected={value!r} actual=<absent>")
                continue
            # WILDCARD in a header value matches any present value —
            # used by G2 upload fixtures for opaque session ids in
            # Location / X-GUploader-UploadID.
            if value == WILDCARD:
                continue
            if actual_norm[key] != value:
                diffs.append(f"headers.{name}: expected={value!r} actual={actual_norm[key]!r}")

    return HttpCompareReport(ok=not diffs, diffs=diffs)


def _diff_body(
    expected: object,
    actual: object,
    *,
    path: str,
    diffs: list[str],
) -> None:
    """Recursive body diff with WILDCARD + structural-subset semantics."""
    if expected == WILDCARD:
        return

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            diffs.append(f"{path}: expected object, actual={_describe(actual)}")
            return
        for key, expected_value in expected.items():
            sub_path = f"{path}.{key}"
            if expected_value == WILDCARD:
                # WILDCARD accepts absent OR present — recorded
                # opaque values like job ids may not always appear in
                # the emulator's response.
                continue
            if key not in actual:
                diffs.append(f"{sub_path}: expected={_describe(expected_value)} actual=<absent>")
                continue
            _diff_body(expected_value, actual[key], path=sub_path, diffs=diffs)
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            diffs.append(f"{path}: expected list, actual={_describe(actual)}")
            return
        if len(expected) != len(actual):
            diffs.append(
                f"{path}: list length mismatch expected={len(expected)} actual={len(actual)}"
            )
            # Don't dive into per-element diffs when lengths differ —
            # the indices wouldn't line up.
            return
        for idx, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=True)):
            _diff_body(expected_item, actual_item, path=f"{path}[{idx}]", diffs=diffs)
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
    the recorded baseline (job ids, etags, timestamps, opaque self-
    links). Paths use the dotted convention shared with
    :func:`tests.conformance._http_corpus.resolve_dotted_path`, plus a
    list-of-objects shortcut: a path containing ``[]`` matches every
    element of the list at that point.

    Examples::

        mask_volatile_fields(body, ("jobReference.jobId",))
        # → body["jobReference"]["jobId"] = WILDCARD
        mask_volatile_fields(body, ("jobs[].id",))
        # → for j in body["jobs"]: j["id"] = WILDCARD

    A path that doesn't resolve is silently skipped — the recorder
    should not fail just because a specific BigQuery response omitted
    one of the canonical opaque keys.
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
                    # Mask each list element directly is uncommon; we
                    # log nothing and skip.
                    continue
                _apply_mask(item, tail)
            return
        if not tail:
            cursor[head] = WILDCARD
            return
        _apply_mask(nested, tail)


__all__ = [
    "HttpCompareReport",
    "compare_http_response",
    "mask_volatile_fields",
]
