# dbt-bigquery against `bqemulator`

Runs a `dbt build` cycle (seed → run → test) against `bqemulator`
instead of real BigQuery, using `dbt-bigquery`'s native endpoint
override path.

Pairs with the [dbt integration guide](../../../guides/dbt-integration.md).

## What it demonstrates

- Pointing `dbt-bigquery` at the emulator using `BIGQUERY_EMULATOR_HOST`
  + the `oauth` method with anonymous credentials.
- One seed (`customers.csv`), one staging model
  (`models/staging/stg_customers.sql`), one mart model
  (`models/marts/dim_customers.sql`), and dbt schema tests
  (`not_null`, `unique`).
- `dbt build` runs end to end with no real-GCP credentials.

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

- `profiles.yml` uses `method: oauth` with no service account — the
  emulator accepts anonymous credentials.
- The endpoint override is set via the
  [`BIGQUERY_EMULATOR_HOST`](https://cloud.google.com/bigquery/docs/emulator)
  env var (a GCP-wide convention `dbt-bigquery` respects when it
  constructs its underlying `google-cloud-bigquery` client).
- No real GCP project, dataset, or billing is required.
