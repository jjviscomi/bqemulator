# dbt-bigquery against `bqemulator`

Runs a `dbt build` cycle (seed → run → test) against `bqemulator`
instead of real BigQuery, using `dbt-bigquery`'s native endpoint
override path.

Pairs with the [dbt integration guide](../../../guides/dbt-integration.md).

## What it demonstrates

- Pointing `dbt-bigquery` at the emulator using `BIGQUERY_EMULATOR_HOST`
  (full URL including the `http://` scheme — dbt-bigquery forwards
  this verbatim into `client_options.api_endpoint` and `requests`
  needs the scheme to pick a connection adapter) + the
  `oauth-secrets` method with a placeholder bearer token (bqemulator
  accepts any token; `oauth-secrets` is the one auth path that does
  not contact Google's `/token` endpoint at startup).
- One seed (`customers.csv`), one staging model
  (`models/staging/stg_customers.sql`), one mart model
  (`models/marts/dim_customers.sql`), and dbt schema tests
  (`not_null`, `unique`).
- `dbt build` runs end to end with no real-GCP credentials.

## Version compatibility

The example tracks `dbt-bigquery >=1.9,<1.11`. Both 1.9 and 1.10
emit `CREATE SCHEMA \`proj\`.\`ds\`` two-part identifiers; the
bqemulator table-rewriter collapses those into a single
`"proj__ds"` schema (matching what every other rewriter path
emits) so the SQL reaches DuckDB cleanly. Earlier passes mistakenly
pinned to `<1.10` chasing what looked like a dbt regression — the
actual cause was bqemulator's rewriter producing `"proj__ds".""`
with an empty trailing identifier.

## Layout

```
dbt_project.yml          — project + model config
profiles.yml             — emulator profile (endpoint, anonymous auth)
seeds/customers.csv      — three rows of test data
models/staging/          — stg_customers.sql + schema.yml
models/marts/            — dim_customers.sql + schema.yml
macros/                  — (none yet — placeholder for project macros)
run.sh                   — start emulator, run `dbt build`, tear down
```

## Run

```bash
make test
```

`make test` invokes `run.sh`, which:

1. Spins up an ephemeral emulator on a random port via the
   `bqemulator` CLI (`bqemulator start --ephemeral`).
2. Exports `BIGQUERY_EMULATOR_HOST` so `dbt-bigquery`'s
   [endpoint resolution](https://github.com/dbt-labs/dbt-bigquery)
   picks it up.
3. Runs `dbt deps && dbt build --profiles-dir . --target emulator`.
4. Asserts a non-zero number of rows landed in `dim_customers`.
5. Tears down the emulator.

## What to look for

- `profiles.yml` uses `method: oauth-secrets` with a literal
  placeholder token (`bqemu-fake-token`). dbt builds a static
  `google.oauth2.credentials.Credentials` from it and never calls
  Google's token endpoint — the bearer reaches bqemulator, which
  doesn't validate auth, and the call goes through.
- The endpoint override is set via the `BIGQUERY_EMULATOR_HOST`
  env var (the Google Cloud Python client libraries recognise it
  natively), prefixed with `http://`. dbt-bigquery forwards the value
  verbatim into `client_options.api_endpoint`, so the scheme must
  be present (the bare `google-cloud-bigquery` library auto-injects
  `http://` but dbt's wrapper bypasses that branch).
- No real GCP project, dataset, or billing is required.
