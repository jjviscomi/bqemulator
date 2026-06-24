# ADR 0047: BigQuery ML surface (metadata, Models REST, ML.PREDICT shape)

- **Status**: Accepted
- **Supersedes**: 0012 (partially: the surface-only slice below moves into scope;
  real training, evaluation, and inference accuracy remain out of scope)
- **Superseded by**: none

## Context

[RFC 0002](../rfcs/0002-bigquery-ml-surface.md) proposes a **surface-only** slice
of BigQuery ML: register model metadata from `CREATE MODEL`, serve it through the
Models REST resource, and make `ML.PREDICT` return correctly-shaped but
deterministic, non-real output. This ADR records the implementation decisions.

[ADR 0012](0012-bqml-out-of-scope.md) put all of BQML out of scope for v1 and
returned `UnsupportedFeatureError` (HTTP 501) for `CREATE MODEL` / `ML.*`. It also
stated that "Models resource CRUD ... is supported." Two corrections of record:
the Models surface was **never implemented** (no route, no catalog entity), and
the real BigQuery Models REST API has **no `insert` method** (models are created
only via `CREATE MODEL` query jobs). ADR 0012 noted the decision was
"reconsiderable for v2"; this ADR is that reconsideration, scoped to the surface
only.

`CREATE MODEL` and `ML.PREDICT` are addressable in the SQLGlot AST (`exp.Create`
with `kind == "MODEL"`, and the dedicated `exp.Predict` node), so they can be
intercepted exactly the way `EXPORT DATA` (`exp.Export`) is
([ADR 0043](0043-export-data-statement.md)). `ML.EVALUATE` / `ML.FORECAST` do not
parse and stay on the 501 path.

## Decisions

### 1. Surface-only scope; training stays out

`CREATE MODEL` registers metadata and derives the feature/label schema by
planning the training query; it does **not** train. `ML.PREDICT` returns
deterministic, intentionally non-real prediction values. `ML.EVALUATE`,
`ML.FORECAST`, `ML.GENERATE_*`, `ML.WEIGHTS`, and `TRANSFORM()` remain out of
scope (still 501). This supersedes ADR 0012 only for the surface; ADR 0012's
training/inference exclusion still governs.

### 2. Intercept `exp.Create(kind=MODEL)` and `exp.Predict` pre-translation, dual-wired

Classification (`_classify_parsed_tree`) maps `exp.Create` with `kind == "MODEL"`
to `"CREATE_MODEL"`. Shared `parse_create_model` / `_execute_create_model_job`
and `_execute_predict` helpers live in `src/bqemulator/jobs/executor.py` and are
invoked from both `execute_query_job` (standalone) and the scripting interpreter,
mirroring the `EXPORT DATA` dual-wiring. `"CREATE MODEL"` and `"ML.PREDICT"` are
removed from `_UNSUPPORTED_KEYWORDS` in `src/bqemulator/sql/translator.py`; the
AST interception replaces the keyword reject. The training query and the
`ML.PREDICT` input query run through the shared
`sql/inner_query.py::rewrite_and_translate_statement` pipeline so every rewrite
rule, row-access policy, and parameter applies as to a bare `SELECT`.

### 3. `ModelMeta` catalog entity + Models REST resource (no insert)

A frozen, dataset-scoped `ModelMeta` (`(project_id, dataset_id, model_id)`) is
added to `catalog/models.py`, with `list/get/create/update/delete_models` across
the repository protocol and its in-memory and DuckDB-backed implementations, a
`_bqemulator_catalog.models` persistence table, and cascade-delete on dataset
drop. `src/bqemulator/api/routes/models.py` exposes `list` / `get` / `patch` /
`delete` (no `insert`, matching BigQuery), modeled on the Routines resource. The
resource is REST-only; the gRPC adapter is untouched.

### 4. Faithful shapes recorded; `ML.PREDICT` values are a documented divergence

The Models REST shape, the `CREATE_MODEL` job/statementType, the `ML.PREDICT`
output column shape, and the error envelopes are recorded from real BigQuery and
asserted exactly. `ML.PREDICT` numeric values cannot match (no training), so
those fixtures are pinned in `tests/conformance/divergences.py` as
`xfail(strict=True)` citing this ADR, per
[ADR 0023](0023-conformance-divergence-baseline.md). Removing the entry when a
future accuracy slice lands makes the fixture pass.

### 5. Disposition + error parity

`CREATE MODEL` onto an existing model errors `duplicate` (HTTP 409);
`IF NOT EXISTS` is a no-op; `OR REPLACE` replaces. Missing parent dataset errors
`notFound` (HTTP 404). `ML.PREDICT` on a missing model errors `notFound`
(HTTP 404). Unknown/invalid OPTIONS error `invalidQuery` (HTTP 400). These
envelopes are pinned by recorded conformance fixtures.

## Consequences

### Capability matrix shift

`CREATE MODEL` (metadata), the Models REST resource (`list`/`get`/`patch`/
`delete`), and `ML.PREDICT` (shape) move from unsupported to supported-surface.
`out-of-scope.md` is updated: the BQML section keeps training, evaluation,
forecasting, generation, and prediction-accuracy out of scope and removes the
inaccurate "Models insert is supported" claim.

### Coverage + test surface

New modules target complete branch coverage; every error path is tested; the
`CREATE MODEL` / `ML.PREDICT` execution logic and the Models repository join the
mutation tier ([ADR 0026](0026-mutation-tier-design-contract.md)). Property tests
cover `CREATE OR REPLACE` idempotency, `ModelMeta` REST round-trip, persistence
save/reload round-trip, and the `ML.PREDICT` row-count and passthrough
invariants. Scripted `CREATE MODEL` / `ML.PREDICT` tests prove the dual-wiring.

### Honesty about prediction values

`ML.PREDICT` values are deterministic placeholders, not real predictions. The
guide and the reference docs carry a prominent callout; the value is chosen to be
obviously synthetic so it is not mistaken for accurate output.

## Unresolved questions

- The exact `statistics.query` field set for `CREATE_MODEL` and the precise
  `ML.PREDICT` output column names/types per model task, resolved by recording.
- The fixed placeholder value for `ML.PREDICT` predictions.
- Which OPTIONS BigQuery echoes on the model resource versus drops as
  training-only.

## Alternatives considered

- **Full BQML training** (rejected here): comparable to the rest of the emulator
  in effort; left to a future RFC.
- **Keep the clean 501** (rejected): blocks Models API and SQL-shape testing,
  which is the common local need.
- **Plausible prediction values** (rejected): invites mistaking stubs for real
  output; a clearly-synthetic deterministic value is safer.
- **Add a Models `insert`** (rejected): BigQuery has none; it would be a
  non-parity invention.

## Related work

- [ADR 0012](0012-bqml-out-of-scope.md): superseded in part by this ADR.
- [ADR 0043](0043-export-data-statement.md): the statement-interception pattern
  reused here.
- [ADR 0023](0023-conformance-divergence-baseline.md): the divergence mechanism
  for `ML.PREDICT` values.
- [ADR 0026](0026-mutation-tier-design-contract.md): the mutation tier the new
  logic joins.

## References

- [RFC 0002](../rfcs/0002-bigquery-ml-surface.md).
- [BigQuery Models REST resource](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/models),
  [CREATE MODEL](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-create),
  [ML.PREDICT](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-predict).
