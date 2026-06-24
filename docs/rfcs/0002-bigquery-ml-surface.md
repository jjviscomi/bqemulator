---
rfc: "0002"
title: "BigQuery ML surface (metadata, Models REST, ML.PREDICT shape)"
status: Accepted
authors:
  - "@jjviscomi"
created: 2026-06-24
updated: 2026-06-24
supersedes: null
superseded-by: null
---

# RFC 0002: BigQuery ML surface (metadata, Models REST, ML.PREDICT shape)

> Accepted under the maintainer fast-track described in the
> [RFC lifecycle](README.md): the design is ratified before drafting and
> implementation proceeds in a phased PR series. The implementation outcome is
> recorded in [ADR 0047](../adr/0047-bigquery-ml-surface.md), which partially
> supersedes [ADR 0012](../adr/0012-bqml-out-of-scope.md).

## Summary

Add a **surface-only** slice of BigQuery ML so that BQML SQL and the Models API
can be exercised against the emulator without a real training runtime:

1. `CREATE MODEL [IF NOT EXISTS | OR REPLACE] ... OPTIONS(...) AS query_statement`
   runs as a `QUERY` job (`statementType` `CREATE_MODEL`) that registers a model
   in the catalog. The training query is parsed and validated through the normal
   pipeline to derive the feature/label schema, but **no model is trained**.
2. The BigQuery **Models REST resource** (`list` / `get` / `patch` / `delete`,
   matching the real API, which has **no** `insert`) serves that metadata.
3. `ML.PREDICT(MODEL ref, (query_statement | TABLE ref))` runs the input query
   and returns its rows plus deterministic, **explicitly non-real** prediction
   column(s) shaped like BigQuery's output.

Real training, evaluation, and inference accuracy stay out of scope (they remain
governed by [ADR 0012](../adr/0012-bqml-out-of-scope.md)). `ML.EVALUATE`,
`ML.FORECAST`, `ML.GENERATE_*`, `ML.WEIGHTS`, the `TRANSFORM()` clause, and all
non-trivial model types beyond metadata registration are out of scope for this
RFC. This RFC establishes the architecture and the parity model that a later,
separate RFC for accuracy-bearing classical models (linear/logistic regression,
k-means) can extend.

## Motivation

Today `CREATE MODEL` and `ML.PREDICT` are rejected by the
`_UNSUPPORTED_KEYWORDS` quick-reject in `src/bqemulator/sql/translator.py` with
an `UnsupportedFeatureError` (HTTP 501). That is correct and clear, but it leaves
two real gaps for users whose pipelines touch BQML:

- **No Models API at all.** [ADR 0012](../adr/0012-bqml-out-of-scope.md) states
  that "Models resource CRUD ... is supported," but in fact **no Models surface
  exists** in the codebase (no route, no catalog entity), and the BigQuery Models
  REST API has **no `insert` method** (models are created only via `CREATE MODEL`
  jobs). A pipeline that lists or reads model metadata cannot run against the
  emulator, and the documentation overstates current support.
- **No way to exercise BQML SQL locally.** A dbt model, scheduled query, or
  orchestration DAG that contains `CREATE MODEL` or `ML.PREDICT` cannot be
  parsed, planned, or shape-checked offline. The common local-testing need is
  not numeric accuracy (that is validated against real BigQuery); it is "does my
  SQL parse, does the model register, does `ML.PREDICT` return the columns my
  downstream query expects, and does the Models API behave."

Doing nothing keeps BQML a hard 501 wall and keeps the inaccurate "Models CRUD
is supported" claim in `out-of-scope.md`. This RFC closes the surface gap while
being honest that values are not real predictions.

## Guide-level explanation

Register a model from a training query, then read it back and predict with it:

```sql
-- Register a model (no training happens; metadata + schema only)
CREATE MODEL my_dataset.my_model
OPTIONS (model_type = 'LINEAR_REG', input_label_cols = ['label']) AS
SELECT feature_1, feature_2, label FROM my_dataset.training;

-- Predict: input rows are returned with prediction column(s) appended
SELECT * FROM ML.PREDICT(MODEL my_dataset.my_model,
                         (SELECT feature_1, feature_2 FROM my_dataset.scoring));
```

The Models REST API serves the registered metadata:

```text
GET    /bigquery/v2/projects/{p}/datasets/{d}/models             -> list
GET    /bigquery/v2/projects/{p}/datasets/{d}/models/{m}         -> get
PATCH  /bigquery/v2/projects/{p}/datasets/{d}/models/{m}         -> patch (description, labels, expirationTime)
DELETE /bigquery/v2/projects/{p}/datasets/{d}/models/{m}         -> delete
```

`bq ls -m my_dataset`, `bq show -m my_dataset.my_model`, and the client-library
`get_model` / `list_models` / `update_model` / `delete_model` calls work against
these endpoints.

**What is real and what is not.** The model's existence, identity
(`modelReference`), `modelType`, timestamps, ETag, and the feature/label column
schema derived from the training query are faithful. `CREATE MODEL` runs as a
real `QUERY` job with `statementType` `CREATE_MODEL`. **The prediction values
returned by `ML.PREDICT` are deterministic placeholders, not real model
output**, and are documented as such everywhere they appear. Use real BigQuery
when prediction accuracy matters.

`CREATE MODEL` and `ML.PREDICT` also work inside scripts (`BEGIN ... END`),
because they flow through the same statement-interception path as a standalone
query job (the path the `EXPORT DATA` statement already uses).

## Reference-level explanation

### Interception architecture

`CREATE MODEL` and `ML.PREDICT` are intercepted **before translation**, mirroring
the existing `EXPORT DATA` design (see
[ADR 0043](../adr/0043-export-data-statement.md)). SQLGlot parses the two
constructs into addressable AST nodes:

- `CREATE MODEL ...` parses to `exp.Create` with `args["kind"] == "MODEL"`, the
  OPTIONS in `properties`, the training query in `.expression`, and a `replace`
  flag for `CREATE OR REPLACE`.
- `ML.PREDICT(MODEL ref, (...))` parses to a dedicated `exp.Predict` node holding
  the model `Table` and the input `Subquery` or `TABLE`.

(`ML.EVALUATE` / `ML.FORECAST` do not parse in the GoogleSQL dialect and stay on
the `_UNSUPPORTED_KEYWORDS` 501 path.) Detection and the parse/execute helpers
live in `src/bqemulator/jobs/executor.py` and are invoked from **both** the
standalone job entry point (`execute_query_job`) **and** the scripting
interpreter, the same dual-wiring `EXPORT DATA` uses, so standalone and scripted
statements share one code path. `"CREATE MODEL"` and `"ML.PREDICT"` are removed
from `_UNSUPPORTED_KEYWORDS`; the AST interception replaces the keyword reject.

### CREATE MODEL

- **OPTIONS.** `model_type` is required (any BigQuery model-type string is
  accepted and stored verbatim; this RFC does not train any of them).
  `input_label_cols` (a string array) names the label column(s); the remaining
  output columns of the training query are the feature columns. Other OPTIONS are
  stored as opaque metadata where BigQuery echoes them and rejected when clearly
  invalid. Unknown top-level OPTIONS are rejected with a clear `InvalidQueryError`
  rather than silently dropped.
- **Schema derivation.** The training query (`.expression`) runs through the
  normal single-statement pipeline
  (`sql/inner_query.py::rewrite_and_translate_statement` plus the call-site
  qualification and binding) to validate it and obtain its result schema. Columns
  named in `input_label_cols` become label columns; the rest become feature
  columns. No rows are trained on; the query is planned, not persisted as data.
- **Disposition.** `CREATE MODEL` onto an existing model errors (`duplicate`,
  HTTP 409, matching BigQuery `Already Exists`). `CREATE MODEL IF NOT EXISTS` is
  a no-op when the model exists. `CREATE OR REPLACE MODEL` replaces it. A missing
  parent dataset errors `notFound` (HTTP 404).
- **Result.** Zero result rows; `statistics.query.statementType` is
  `CREATE_MODEL`. The exact additional `statistics.query` fields are pinned by
  conformance recording (see *Parity model*).

### ML.PREDICT

- **Model resolution.** The `MODEL ref` is resolved against the catalog. A
  missing model errors the way BigQuery does (`notFound`, HTTP 404).
- **Execution.** The input query (subquery or `TABLE`) runs through the normal
  pipeline. Output is the input rows with prediction column(s) appended, named to
  match BigQuery's shape for the model's task (for example `predicted_<label>`
  for a regressor). Input columns are preserved (passthrough).
- **Values.** Prediction values are **deterministic and intentionally not
  plausible real predictions** (so they are never mistaken for accurate output).
  The exact placeholder is fixed and documented. Row count equals the input row
  count.

### Models REST resource

`/bigquery/v2/projects/{projectId}/datasets/{datasetId}/models[/{modelId}]` with
`list`, `get`, `patch`, `delete`, modeled on the existing Routines resource
(`src/bqemulator/api/routes/routines.py`). **No `insert`** (the real API has
none; models are born from `CREATE MODEL` jobs). The wire resource uses
`modelReference` (`projectId` / `datasetId` / `modelId`), `modelType`,
`creationTime`, `lastModifiedTime`, `etag`, and the derived `featureColumns` /
`labelColumns`. Patch coalesces the mutable fields BigQuery allows (description,
labels, expirationTime). The resource is REST-only; there is no gRPC Models
service in BigQuery, so the gRPC adapter is untouched.

### Catalog + persistence

A new frozen `ModelMeta` catalog entity (dataset-scoped, keyed
`(project_id, dataset_id, model_id)`) is added with `list/get/create/update/
delete_models` repository methods across the in-memory and DuckDB-backed
implementations, a new `_bqemulator_catalog.models` table for persistence, and
cascade-delete when the parent dataset is dropped with `delete_contents=true`.
The REST resource has no create endpoint; `create_model` exists for the
`CREATE MODEL` job and test seeding.

### Parity model

The surface that BigQuery returns **shape-identically regardless of training** is
recorded faithfully from real BigQuery and asserted exactly:

- the Models REST resource shape (`list` / `get`),
- the `CREATE MODEL` job resource and `statementType`,
- the `ML.PREDICT` output **column shape** (names, order, types),
- the error envelopes (model not found, duplicate, invalid OPTIONS, missing
  dataset).

The one place the emulator cannot match BigQuery is the **numeric value** of
`ML.PREDICT` predictions. Those fixtures are recorded from real BigQuery and
pinned as a documented divergence in
`tests/conformance/divergences.py` (`pytest.mark.xfail(strict=True)`, citing
ADR 0047), exactly as other deliberate divergences are handled
([ADR 0023](../adr/0023-conformance-divergence-baseline.md)). When a future RFC
adds real classical-model inference, removing that divergence entry makes the
fixture pass on the next run.

## Drawbacks

- **Predictions are fake.** `ML.PREDICT` returns placeholder values. This is the
  defining limitation of the surface-only scope; it is mitigated by making the
  values obviously non-real and documenting it prominently, but a user who does
  not read the docs could be surprised. The alternative (no `ML.PREDICT` at all)
  is worse for pipeline testing.
- **Partial parity is a sharper edge than a clean 501.** A hard
  `UnsupportedFeatureError` is unambiguous; a statement that "works" but returns
  fake numbers requires the user to understand the boundary. The divergence
  registry and docs callouts carry that weight.
- **Scope-creep gravity.** Once `CREATE MODEL` registers metadata, the natural
  pull is toward real training (Option B). This RFC draws the line explicitly and
  leaves training to a separate, future RFC.
- **Surface coupling.** `CREATE MODEL` reuses the inner-query pipeline and the
  `EXPORT DATA` interception pattern; a regression in that shared path could
  affect models. Mitigated by tests on both standalone and scripted paths.

## Rationale and alternatives

- **Surface-only first** (chosen) vs. full BQML vs. nothing. Full BQML training
  is comparable in effort to the rest of the emulator
  ([ADR 0012](../adr/0012-bqml-out-of-scope.md)) and is not undertaken here.
  Surface-only unblocks the most common local-testing needs (SQL parsing, Models
  API, pipeline shape) at a fraction of the cost and lays the architecture for a
  later accuracy slice.
- **AST interception pre-translation** (chosen) vs. translator keyword pass vs. a
  new REST job type. The chosen point preserves OPTIONS from the AST, runs the
  inner query through the real pipeline, and covers standalone and scripted
  statements with one path, exactly as `EXPORT DATA` does.
- **Obviously-non-real placeholder values** (chosen) vs. plausible-looking
  numbers vs. NULL. A plausible number invites mistaking stubs for real output;
  NULL collides with legitimately-null passthrough columns and hides the
  prediction column's presence. A fixed, clearly-synthetic value is safest.
- **Models REST without `insert`** (chosen, matches BigQuery) vs. adding an
  `insert` for convenience. BigQuery has no `models.insert`; adding one would be
  a non-parity invention.

## Prior art

- [ADR 0012](../adr/0012-bqml-out-of-scope.md): the original BQML out-of-scope
  decision, which this RFC partially reverses (surface-only in; training out) and
  whose inaccurate "Models insert is supported" claim it corrects.
- [ADR 0043](../adr/0043-export-data-statement.md) / RFC 0001: the
  pre-translation statement-interception pattern (`exp.Export`, dual-wired into
  standalone and scripted paths) that this RFC reuses for `exp.Create(kind=MODEL)`
  and `exp.Predict`.
- [ADR 0023](../adr/0023-conformance-divergence-baseline.md): the documented
  `xfail(strict=True)` divergence mechanism used here for `ML.PREDICT` values.
- `src/bqemulator/api/routes/routines.py`: the dataset-scoped REST resource
  template the Models resource mirrors.
- Real BigQuery:
  [Models REST resource](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/models),
  [CREATE MODEL](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-create),
  and
  [ML.PREDICT](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-predict).

## Unresolved questions

- The exact `statistics.query` field set for a `CREATE_MODEL` job (beyond
  `statementType`) and the precise `ML.PREDICT` output column names/types per
  model task are resolved by conformance recording from real BigQuery, then fed
  back into the implementation.
- The precise fixed placeholder value for `ML.PREDICT` predictions (a sentinel
  that is unambiguous yet type-compatible with the prediction column type).
- Which OPTIONS BigQuery echoes back on the model resource versus which are
  training-only and dropped.

## Future possibilities

- Real classical-model inference (linear/logistic regression, k-means) with
  approximate-parity numeric output, via scikit-learn / statsmodels behind an
  optional dependency extra (the "Option B" follow-on). This removes the
  `ML.PREDICT`-value divergence for the covered model types.
- `ML.EVALUATE`, `ML.WEIGHTS`, `ML.FEATURE_INFO`, `ML.TRAINING_INFO` over the
  registered metadata.
- `ML.GENERATE_*` (LLM-backed remote models) as a separate, network-dependent
  surface.
