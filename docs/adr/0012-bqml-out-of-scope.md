# ADR 0012: BigQuery ML explicitly out of scope for v1

- **Status**: Accepted (partially superseded by [ADR 0047](0047-bigquery-ml-surface.md))
- **Superseded by**: 0047 (surface-only slice: `CREATE MODEL` metadata, Models
  REST `list`/`get`/`patch`/`delete`, and `ML.PREDICT` output shape move into
  scope; training, evaluation, forecasting, generation, and prediction accuracy
  remain out of scope under this ADR). Note: this ADR's claim that "Models
  resource CRUD ... insert ... is supported" was never accurate (the Models
  surface was unimplemented, and BigQuery has no `models.insert`); ADR 0047
  implements `list`/`get`/`patch`/`delete` only.

## Context

BigQuery ML (`CREATE MODEL`, `ML.PREDICT`, `ML.EVALUATE`, `ML.FORECAST`,
clustering, ARIMA, linear/logistic regression, DNN, boosted trees, matrix
factorization, AutoML) is a large surface area inside BigQuery.

## Decision

BQML is explicitly **out of scope for v1.0.0**. `CREATE MODEL` and `ML.*`
function invocations return a clear `UnsupportedFeatureError` with
pointer to this ADR and `docs/reference/out-of-scope.md`.

Only Models resource CRUD (list/get/insert/patch/update/delete of model
metadata) is supported — users can register externally-trained models as
metadata and test their workflows around that metadata.

## Rationale

- Emulating correct BQML training/inference would require shipping ML
  runtimes (scikit-learn, TensorFlow, statsmodels, etc.) and reproducing
  BigQuery's model semantics to a degree that approximates
  production-correct predictions. This is comparable in effort to the
  rest of the emulator combined.
- The most common testing needs for BQML users — workflow orchestration
  (DAGs, scheduled queries, service-account behavior) — are covered by
  testing against real BigQuery in a dedicated test project with the
  sandbox tier.

## Consequences

- **Positive**: keeps v1 scope tractable and quality bar achievable.
- **Positive**: clear error messages prevent silent wrong behavior.
- **Negative**: users whose primary workload is BQML cannot test locally.
  Documented; reconsiderable for v2 as a separate product decision.
