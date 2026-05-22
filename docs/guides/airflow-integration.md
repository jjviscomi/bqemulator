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
    gcp_conn_id="google_cloud_default",
)
```

## Wiring the hook to the emulator

Two env vars + one monkey-patch is the full surface in test code.

```python
import os
import google.auth
import google.auth._default
import google.auth.credentials

# 1. Tell the BQ client to talk to bqemulator instead of Google.
#    The ``http://`` prefix is required — the Airflow Google provider
#    forwards this verbatim into ``client_options.api_endpoint`` and
#    ``requests`` aborts without a scheme.
os.environ["BIGQUERY_EMULATOR_HOST"] = "http://localhost:9050"

# 2. Configure the Airflow connection. The extras carry the project
#    and scope; ``key_path`` is left empty because we replace the
#    whole credential-resolution path in step 3.
os.environ["AIRFLOW_CONN_GOOGLE_CLOUD_DEFAULT"] = (
    'google-cloud-platform://?{"project":"bqemu-demo","key_path":""'
    ',"scope":"https://www.googleapis.com/auth/bigquery"}'
)

# 3. Make ``google.auth.default()`` return AnonymousCredentials so
#    the hook never tries to exchange a JWT against
#    ``oauth2.googleapis.com/token`` (a synthetic SA fails there
#    with ``invalid_grant: account not found`` — bqemulator doesn't
#    serve the OAuth token endpoint).
anon = google.auth.credentials.AnonymousCredentials()
def _emu_default(scopes=None, request=None, quota_project_id=None,
                 default_scopes=None):
    return anon, "bqemu-demo"
google.auth.default = _emu_default
google.auth._default.default = _emu_default
```

Test DAGs using Airflow's `dag.test()` / `TaskInstance.run(test_mode=True)`.
