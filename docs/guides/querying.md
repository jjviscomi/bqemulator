# Querying

Status: shipped.

```python
query = client.query("""
    SELECT user_id, SUM(amount) AS total
    FROM sales.orders
    WHERE placed_at > TIMESTAMP('2024-01-01')
    GROUP BY user_id
    ORDER BY total DESC
    LIMIT 10
""")
for row in query.result():
    print(row.user_id, row.total)
```

Supported SQL features track the
[compatibility matrix](../reference/compatibility-matrix.md). SQL-function
mapping is documented in
[sql-function-mapping.md](../reference/sql-function-mapping.md).

## Caching

Identical queries return cached results within the configured TTL
(`BQEMU_QUERY_CACHE_TTL_SECONDS`, default 24h). The cache is invalidated
automatically when base tables change (via `TableDataChanged` events).

Set `use_query_cache=False` on the job configuration to bypass.

## Dry-run

```python
job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
job = client.query(sql, job_config=job_config)
print(job.total_bytes_processed)
```

Dry runs perform full SQL validation but do not execute; the byte estimate
is derived from catalog `num_bytes` statistics for referenced tables.
