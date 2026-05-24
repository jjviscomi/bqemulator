# Conformance tier (Tier 5)

This document maps the conformance tier's modules to the contracts
they enforce. For the design rationale see
[ADR 0022](../adr/0022-conformance-corpus-design.md); for the
operator-facing guide see [conformance-corpus.md](../guides/conformance-corpus.md).

## What the tier does

For every query in `tests/conformance/sql_corpus/`:

1. **Record (one-time, manual)** — `scripts/record_conformance_fixtures.py`
   runs the query against real BigQuery, captures the output rows
   and schema, and writes them to `expected.json` alongside the
   BigQuery job id, the bytes-processed count, and the wall-clock
   duration.
2. **Replay (CI, per-PR + ad-hoc)** — `tests/conformance/test_corpus.py`
   parametrises one pytest test per fixture, runs the query against
   the in-process emulator, and diffs the result against the
   recorded baseline with type-aware tolerance. The replay is
   offline (no BigQuery credentials required); the per-PR gate is
   wired into `.github/workflows/ci.yml`. The standalone
   `.github/workflows/conformance.yml` workflow exists for ad-hoc
   `workflow_dispatch` invocation against a specific branch (typical
   use case: verifying a recording-session PR before opening it).

The corpus currently ships **1141 active SQL fixtures + 48 HTTP +
26 gRPC = 1215 active fixtures** (plus 18 INFORMATION_SCHEMA
stubs awaiting operator-side recording). 13 fixtures are pinned
XFAIL in `tests/conformance/divergences.py` as permanent
design-decision divergences. The pass-rate gate is **100%
non-divergent** — every fixture must either pass or be in the
divergences registry. ADR 0022 §5 documents the gate in detail.

## Module map

```
tests/conformance/
    __init__.py            # tier-level docstring + rule summary
    conftest.py            # shared fixtures, marker enforcement, seed
    _corpus.py             # fixture discovery + ${DATASET} substitution
    _comparison.py         # type-aware row + schema diff
    _row_encoding.py       # rows ⇆ JSON (shared with the recorder)
    divergences.py         # explicit xfail registry
    test_corpus.py         # parametrised runner
    sql_corpus/
        README.md
        <surface>/
            <fixture>/
                query.sql
                setup.sql
                expected.json
scripts/
    record_conformance_fixtures.py
```

| Module | Responsibility |
|---|---|
| `_corpus.py` | Walks `sql_corpus/`, builds an immutable `Fixture` per directory, exposes `substitute_dataset(sql, dataset)` and `split_statements(script)` so both the recorder and the runner produce the same SQL stream. |
| `_comparison.py` | The `compare_results(expected, actual_rows, actual_schema) -> CompareReport` entry point. Dispatches per BigQuery type to the rule documented in ADR 0022 §3 (exact for ints, ULP-scale for floats, ±1 µs for datetimes, etc.). |
| `_row_encoding.py` | Encodes `google-cloud-bigquery`'s Python objects into the recorder's JSON shape. Shared by the runner so the actual emulator output is normalised the same way before diff. |
| `divergences.py` | Constants-only module: `KNOWN_DIVERGENCES: dict[str, str]` mapping fixture id → ADR-rooted rationale. Adding an entry pins the fixture to `xfail(strict=True)`; removing one un-pins it. |
| `test_corpus.py` | Discovers fixtures once at import time and parametrises one pytest test per fixture. For fixtures with a `setup.sql`, creates a per-test dataset on the emulator, executes setup, runs the query, drops the dataset. Literal-only fixtures skip dataset creation. |
| `conftest.py` | Auto-applies the `conformance` marker; seeds the RNG (the conformance tier does not generate random inputs but the seed is logged in the session header for parity with the chaos tier). Exposes the `conformance_dataset` helper fixture for ad-hoc tests not parametrised by the corpus. |
| `scripts/record_conformance_fixtures.py` | The recorder. Walks the same corpus, executes against a real BigQuery project, writes `expected.json`. Enforces the byte-scan cap, refuses to overwrite without `--force`, logs every `(fixture, job_id, bytes, ms)` tuple. |

## Fixture data shape

Two shapes exist; the runner branches on the optional ``error``
field's presence (see ADR 0022 §3 ``Error parity``).

`expected.json` (version 1 — success / row fixtures):

```json
{
  "fixture_version": 1,
  "bigquery": {
    "project": "<recording project>",
    "job_id": "<BigQuery job id>",
    "location": "US",
    "total_bytes_processed": 1024,
    "total_bytes_billed": 10485760,
    "duration_ms": 1385
  },
  "schema": [
    {"name": "n", "type": "INT64", "mode": "NULLABLE"}
  ],
  "rows": [
    {"n": 42}
  ],
  "row_count": 1,
  "duration_class": "fast"
}
```

`expected.json` (version 2 — error fixtures):

```json
{
  "fixture_version": 2,
  "bigquery": {
    "project": "<recording project>",
    "job_id": null,
    "location": "US",
    "total_bytes_processed": 0,
    "total_bytes_billed": 0,
    "duration_ms": 525
  },
  "error": {
    "reason": "notFound",
    "location": null,
    "http_status": 404,
    "message_pattern": "Not\\ found:\\ Table\\ [\\w\\-\\.:]+\\.t_does_not_exist_xyz\\ was\\ not\\ found\\ in\\ location\\ US",
    "message_sample": "Not found: Table myproj:ds.t_does_not_exist_xyz was not found in location US"
  },
  "duration_class": "fast"
}
```

The v2 shape adds an optional ``error`` envelope alongside (and
exclusive of) ``schema`` + ``rows``. The runner branches on the
field's presence:

* No ``error`` field ⇒ success-expected (the v1 contract; runner
  diffs rows + schema using ``compare_results``).
* ``error`` field present ⇒ error-expected (runner expects the
  emulator to raise ``GoogleAPIError`` and diffs via
  ``compare_error``: ``reason`` / ``location`` / ``http_status``
  exact match; ``message`` regex-matched against
  ``message_pattern`` via ``re.search`` with DOTALL).

The 644 pre-existing v1 fixtures stay backward-compatible because
they lack an ``error`` field. Only newly-recorded fixtures get
``fixture_version`` 2.

The `bigquery.*` block is metadata only — the runner reads it solely
for diagnostic messages. The comparison is driven by `schema` and
`rows`. The recorder always writes both; hand-editing either is a
non-negotiable disqualifier.

## Comparison tolerance summary

Locked by ADR 0022 §3:

| Type | Rule |
|---|---|
| `INT64`, `BOOL`, `STRING`, `BYTES`, `DATE` | Exact equality |
| `NUMERIC`, `BIGNUMERIC` | `Decimal` equality |
| `FLOAT64` | `math.isclose(rel_tol=1e-12, abs_tol=1e-15)` |
| `TIMESTAMP`, `DATETIME`, `TIME` | `abs(a - b) ≤ 1 µs` |
| `GEOGRAPHY` | WKT after whitespace + case normalisation |
| `ARRAY` | Length + ordered element-wise (recursive) |
| `STRUCT` | Per-field (recursive) |
| `RANGE` | Equality on `{"start", "end"}` shape |
| `INTERVAL` | Canonical `YEAR TO SECOND` string |
| `JSON` | Parsed `json.loads` equality |

Schema-level: case-normalised type aliases (`INTEGER` ↔ `INT64`, etc.)
and an absent `mode` is treated as `NULLABLE`.

## Divergence policy

Three rules from ADR 0022 §4:

1. Every divergence has a rationale string in
   `tests/conformance/divergences.py` that references an ADR or
   `docs/reference/out-of-scope.md`.
2. The marker is `xfail(strict=True)` so closing a divergence (the
   emulator catches up) shows up as a test failure on the next run.
3. The fixture stays in the corpus with its recorded baseline; the
   divergence is recorded but the SQL is still exercised against the
   emulator.

The initial divergence set (spheroidal-vs-planar GEOGRAPHY at
continental scales) is anchored in
[ADR 0019](../adr/0019-specialized-types.md) and
[`out-of-scope.md`](../reference/out-of-scope.md#spheroidal-geometry-on-geography).

## Runner / recorder symmetry

The recorder and the runner share three modules:

* `_corpus.py` — same discovery + same substitution.
* `_row_encoding.py` — same JSON serialisation for rows and schemas.
* (Implicitly) `_comparison.py` — the recorder writes the same shape
  the runner expects to receive, so the diff path is symmetric.

This is load-bearing: any divergence between the two would produce
spurious test failures unrelated to actual emulator behaviour. The
shared modules eliminate that risk.

## CI integration

The conformance corpus is the project's primary parity-with-BigQuery
guarantee and runs **on every PR + push to main** via the
`conformance` job in
[`.github/workflows/ci.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/ci.yml).
That per-PR gate is the release-blocker check — a regression there
must be resolved before merge. The standalone
[`.github/workflows/conformance.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/conformance.yml)
workflow is `workflow_dispatch` only and exists for ad-hoc operator
invocation against a specific branch (typical use case: verifying a
recording-session PR before opening it via
`gh workflow run conformance --ref <branch>`).

Both invocations are **offline**: neither calls the recorder nor real
BigQuery. There is no GCP service-account key stored in repository
secrets; the workflows simply install the project and run
`pytest tests/conformance -m conformance` against the in-process
emulator with the committed `expected.json` baselines.

A failing run is an alert that either:

* The emulator regressed (most common — fix and re-merge).
* BigQuery itself shipped a change (rare — re-record locally and
  review).
* A divergence was closed (an xfail'd fixture now passes — remove
  the entry from `divergences.py`).

Re-recording is a deliberate **local** action. The maintainer runs
`scripts/record_conformance_fixtures.py` on their workstation
against a real BigQuery project they control, reviews the diff in
their editor, and commits the changed `expected.json` files in a
normal PR. Keeping recording out of CI removes the GCP-key
attack surface and prevents an automated drift-fixer from silently
overwriting baselines.
