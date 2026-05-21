# Flask + BigQuery integration tests with pytest

A minimal Flask service whose only route reads from BigQuery, plus an
integration test suite that exercises the route end-to-end against
`bqemulator`.

This is the canonical example for the
[pytest fixture quickstart](../../../quickstart/pytest.md): you install
`bqemulator[dev]`, your `conftest.py` stays empty, and the
`bqemu_client` fixture is auto-discovered from the installed plugin.

## What it demonstrates

- Wiring a Flask app to BigQuery via the standard
  `google-cloud-bigquery` `Client` — no emulator-specific code in the
  application.
- A pytest fixture (`app_client`) that swaps in the emulator-backed
  client at request time via Flask's dependency-injection hook
  (`app.config["BIGQUERY_CLIENT"]`).
- Seeding test data using the same `Client` the app sees, so tests
  remain transparent to the production code path.
- Session-scoped emulator + per-test dataset isolation.

## Layout

```
src/app.py        — Flask app, /customers route reads from BigQuery
tests/conftest.py — wires bqemu_client into the app for the test session
tests/test_customers.py — end-to-end request/response assertions
```

## Run

```bash
make test
```

`make test` runs `pytest tests/` after ensuring the `flask` extras and
`bqemulator[dev]` are installed.

## What to look for

- No `bqemulator` imports leak into [src/app.py](src/app.py). The app
  is the same code you'd run in production; only the `bigquery.Client`
  injection point differs.
- The `bqemu_client` fixture is **session-scoped** — one emulator per
  pytest session. Per-test isolation is achieved by creating a unique
  dataset per test.
- The fixture also sets `BIGQUERY_EMULATOR_HOST`, so any code path
  inside the app that constructs its own client (rare but possible)
  will still resolve to the emulator.
