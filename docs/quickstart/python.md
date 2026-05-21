# Python quickstart

```bash
pip install bqemulator google-cloud-bigquery
bqemulator start --ephemeral &
```

```python
import os
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery

os.environ["BIGQUERY_EMULATOR_HOST"] = "localhost:9050"

client = bigquery.Client(
    project="my-project",
    credentials=AnonymousCredentials(),
    client_options=ClientOptions(api_endpoint="http://localhost:9050"),
)

client.create_dataset("sales")

schema = [
    bigquery.SchemaField("id", "INT64", "REQUIRED"),
    bigquery.SchemaField("amount", "NUMERIC"),
    bigquery.SchemaField("placed_at", "TIMESTAMP"),
]
table = client.create_table(bigquery.Table("my-project.sales.orders", schema=schema))

client.insert_rows_json(
    table,
    [{"id": 1, "amount": "12.50", "placed_at": "2024-04-15T00:00:00Z"}],
)

for row in client.query("SELECT COUNT(*) AS n FROM sales.orders").result():
    print(row.n)
```

See [pytest quickstart](pytest.md) for the integration-test flow.
