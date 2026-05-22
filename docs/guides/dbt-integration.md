# dbt integration

Status: shipped (runnable example in `docs/examples/python/dbt-local/`).

## Version compatibility

dbt-bigquery **1.9.x** is the pinned target. **1.10** introduced a
DDL-emission regression — `CREATE SCHEMA` against an emulator gets
serialised as
`CREATE SCHEMA "{project}__{dataset}_{custom_schema}".""` (database
holds the full triple, schema is empty), which is malformed SQL and
bqemulator's parser rejects. Tracked for cleanup in
[#16](https://github.com/jjviscomi/bqemulator/issues/16); v1.0.0
ships with the example pinned to `>=1.9,<1.10`.

## Profile

```yaml
bqemu:
  target: dev
  outputs:
    dev:
      type: bigquery
      # ``oauth-secrets`` is the one auth path that never hits
      # ``oauth2.googleapis.com/token`` — it builds a static
      # ``Credentials(token=…)`` and uses the literal bearer.
      # bqemulator accepts any token, so a constant placeholder
      # is fine.
      method: oauth-secrets
      token: bqemu-fake-token
      project: test-project
      dataset: analytics
      threads: 4
      location: US
      priority: interactive
      timeout_seconds: 300
```

Start the emulator and export

```bash
export BIGQUERY_EMULATOR_HOST="http://localhost:9050"
```

The `http://` scheme is **required** — dbt-bigquery forwards
`BIGQUERY_EMULATOR_HOST` verbatim into `client_options.api_endpoint`
without prepending a scheme, and `requests` aborts with
`No connection adapters were found for 'localhost:9050/...'` if it's
missing. The bare `google-cloud-bigquery` client auto-injects the
scheme, but dbt-bigquery's wrapper bypasses that branch.

Then run `dbt run --profiles-dir.` against the local target.

A fully-wired example lives in
`docs/examples/python/dbt-local/` and is CI-verified.
