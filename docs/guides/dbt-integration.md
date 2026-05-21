# dbt integration

Status: shipped (runnable example in `docs/examples/python/dbt-local/`).

dbt-bigquery supports custom `host` and `location` settings. Profile:

```yaml
bqemu:
  target: dev
  outputs:
    dev:
      type: bigquery
      method: oauth
      project: test-project
      dataset: analytics
      threads: 4
      location: US
      priority: interactive
      timeout_seconds: 300
      impersonate_service_account: null
      # bqemulator-specific override
      gcs_bucket: null
      client_options:
        api_endpoint: http://localhost:9050
```

Start the emulator, export `BIGQUERY_EMULATOR_HOST=localhost:9050`, and
run `dbt run --profiles-dir.` against the local target.

A fully-wired example lives in
`docs/examples/python/dbt-local/` and is CI-verified.
