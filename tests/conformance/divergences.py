"""Registry of expected divergences between bqemulator and real BigQuery.

Each entry in :data:`KNOWN_DIVERGENCES` maps a fixture id
(``<phase>/<fixture_name>``, as returned by :attr:`Fixture.id`) to
a rationale string. The conformance runner attaches the rationale
to a ``pytest.mark.xfail(strict=True)`` marker on the matching
fixture.

Contract
--------

* ``strict=True`` is the load-bearing invariant: an unexpected
  pass fails the suite just as an unexpected fail does. The dict
  is the live ledger — an entry exists iff that fixture is
  currently divergent.
* Every rationale references an ADR (e.g., ADR 0019 for
  spheroidal-vs-planar GEOGRAPHY, ADR 0023 for the slice-2
  closure-bucket baseline) or a section of
  ``docs/reference/out-of-scope.md``. Invented divergences are
  forbidden.

Adding an entry
---------------

1. The divergence is rooted in a locked design decision (an ADR)
   or in a catalogued scope-of-work bucket (ADR 0023 for the
   slice-2 baseline).
2. The fixture stays in the corpus with its recorded
   ``expected.json`` so any future change that removes the
   divergence surfaces as an unexpected-pass failure under
   ``strict=True``.

Removing an entry
-----------------

When a slice closes the gap, delete the entry. The xfail marker
disappears with it; the fixture starts passing on the next
conformance run. CI catches both sides: a residual entry against
a now-passing fixture (unexpected-pass), and a missing entry
against a still-failing one (unexpected-fail).

Historical narrative — which buckets closed when, how each
closure shipped, the ratchet count at every milestone — lives in
ADR 0023, the git history of this file, and the release notes in
``CHANGELOG.md``. This module is a current-state registry, not a
closure journal.
"""

from __future__ import annotations

# Rationale constants. Strings reused across multiple entries are
# named here so a single edit updates every pin; the matrix
# generator renders the first sentence of each into
# ``compatibility-matrix.md``.

_SPHEROIDAL = (
    "Spheroidal-vs-planar divergence — see ADR 0019 and "
    "docs/reference/out-of-scope.md#spheroidal-geometry-on-geography"
)
_BIGNUMERIC_CAP = (
    "BIGNUMERIC literal with 39 integer digits exceeds DuckDB's "
    "DECIMAL(38, 0) cap; literals with ≤ 38 integer digits work via "
    "fractional truncation (Path C of numeric_literals.py) — see "
    "docs/reference/out-of-scope.md#bignumeric-literals-with-39-integer-digits"
)
_CTE_SELF_JOIN_WINDOW_UNNEST = (
    "TPC-DS Q47-style multi-CTE pattern: a CTE that carries a window "
    "aggregate (AVG OVER PARTITION BY ... and RANK OVER ...) is self-"
    "joined to itself three times (v1, v1 v1_lag, v1 v1_lead) with "
    "row-number equality joins. SQLGlot inlines the CTE three times; "
    "DuckDB raises ``Binder Error: UNNEST requires a single list as "
    "input`` on the resulting plan. Closure needs an investigation "
    "into how SQLGlot's CTE-inlining transforms window aggregates over "
    "ROW_NUMBER joins — see "
    "docs/reference/out-of-scope.md#cte-self-join-with-window-aggregate-tpc-ds-q47"
)
_HLL_SKETCH_BINARY = (
    "HLL sketch BYTES format differs — see "
    "docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit-merge_partial"
)
_INFO_SCHEMA_IAM = (
    "INFORMATION_SCHEMA.ROW_ACCESS_POLICIES requires "
    "bigquery.rowAccessPolicies.list IAM permission. Real BigQuery "
    "returns 404 NotFound to non-admin callers; emulator returns "
    "the policy row (IAM not enforced per out-of-scope.md#iam-"
    "enforcement). Pinned as a fundamental divergence."
)


# fixture_id → rationale. Keys are unique by construction
# (``test_corpus.py`` parametrises one test per id). Entries are
# grouped by rationale source for review ergonomics; the matrix
# generator sorts by fixture id when rendering.
KNOWN_DIVERGENCES: dict[str, str] = {
    # ADR 0019 — spheroidal-vs-planar GEOGRAPHY backend.
    "specialized_types/st_asbinary_point": _SPHEROIDAL,
    "specialized_types/st_buffer_continental": _SPHEROIDAL,
    "specialized_types/st_centroid_polygon": _SPHEROIDAL,
    "specialized_types/st_intersection_polygons": _SPHEROIDAL,
    "specialized_types/spheroidal_buffer_neighborhood_match": _SPHEROIDAL,
    "specialized_types/spheroidal_buffer_state_xfail": _SPHEROIDAL,
    "specialized_types/spheroidal_buffer_street_match": _SPHEROIDAL,
    # docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit-merge_partial
    "standard_functions/agg_hll_count_init_basic": _HLL_SKETCH_BINARY,
    "standard_functions/agg_hll_count_merge_partial_basic": _HLL_SKETCH_BINARY,
    # docs/reference/out-of-scope.md#bignumeric-literals-with-39-integer-digits
    "standard_functions/bound_bignumeric_max": _BIGNUMERIC_CAP,
    # docs/reference/out-of-scope.md#cte-self-join-with-window-aggregate-tpc-ds-q47
    "standard_functions/tpcds_q47": _CTE_SELF_JOIN_WINDOW_UNNEST,
    # docs/reference/out-of-scope.md#iam-enforcement
    "row_access/caller_information_schema_visibility": _INFO_SCHEMA_IAM,
}


__all__ = ["KNOWN_DIVERGENCES"]
