# Query parameters

Status: shipped.

Positional (`?`) and named (`@name`) parameters, including arrays and
structs, are fully supported.

```python
from google.cloud import bigquery

job_config = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ScalarQueryParameter("min_amount", "NUMERIC", "50"),
        bigquery.ArrayQueryParameter("user_ids", "INT64", [1, 2, 3]),
    ],
)
query = client.query(
    "SELECT * FROM sales.orders "
    "WHERE amount >= @min_amount AND user_id IN UNNEST(@user_ids)",
    job_config=job_config,
)
```

See the [SQL translation architecture](../architecture/sql-translation.md)
for how parameters are bound.
