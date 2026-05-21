# Airflow integration

Status: shipped (runnable example in `docs/examples/python/airflow-dag-test/`).

Airflow's `BigQueryInsertJobOperator` accepts a custom `BigQueryHook`
configuration. Point the hook at the emulator:

```python
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator

task = BigQueryInsertJobOperator(
    task_id="daily_totals",
    configuration={
        "query": {
            "query": "SELECT * FROM sales.orders",
            "useLegacySql": False,
        },
    },
    gcp_conn_id="bqemu_conn",
)
```

Airflow connection `bqemu_conn` is configured with `Extra` set to:

```json
{
  "client_options": {"api_endpoint": "http://localhost:9050"},
  "key_path": null
}
```

Test DAGs using Airflow's `dag.test()` / `TaskInstance.run(test_mode=True)`.
