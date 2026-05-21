# Testing Airflow DAGs against `bqemulator`

Runs an Airflow DAG offline (no scheduler, no webserver) whose tasks
hit `bqemulator` instead of real BigQuery. The pytest suite exercises
the DAG via Airflow's `TaskInstance.run()` API.

Pairs with the [Airflow integration guide](../../../guides/airflow-integration.md).

## What it demonstrates

- A DAG that uses `BigQueryInsertJobOperator` to create a dataset, load
  rows, and run an aggregate query.
- An Airflow connection (`google_cloud_default`) configured at test
  time via `AIRFLOW_CONN_GOOGLE_CLOUD_DEFAULT` so no Airflow metadata
  DB is required.
- Pointing the connection at `bqemulator` via the standard
  `BIGQUERY_EMULATOR_HOST` env var that `google-cloud-bigquery` (the
  hook's underlying transport) respects.
- Tests run each task in isolation via `TaskInstance.run(test_mode=True)`.

## Layout

```
dags/load_customers_dag.py — DAG with three BigQuery tasks
tests/test_load_customers_dag.py — exercises each task against emulator
```

## Run

```bash
make test
```

`make test` runs `pytest tests/`. The `bqemu_server` fixture starts an
in-process emulator for the test session.

## What to look for

- The DAG itself is production-shaped — no emulator-specific code.
- Test isolation: each test creates a unique dataset name via
  `uuid.uuid4()` and cleans up in a teardown.
- We do **not** spin up an Airflow scheduler — we use Airflow's
  task-instance API directly, the recommended pattern for DAG unit
  tests.
