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

## GROUP BY \<alias\>

`GROUP BY` may name a `SELECT`-list alias instead of a real column, even
when a same-named real column exists elsewhere in the join graph — the
alias always wins, matching BigQuery's own resolution rule
([RFC 0003](../rfcs/0003-group-by-alias-resolution.md)):

```sql
SELECT pr.id AS project_id, pr.name AS project_name, SUM(hrs) AS total
FROM placements pl
JOIN tasks t ON t.id = pl.task_id
JOIN processes proc ON proc.id = t.process_id
JOIN projects pr ON pr.id = proc.project_id
GROUP BY project_id, project_name
```

Alias matching is always case-insensitive (`GROUP BY project_id` matches
`AS Project_ID`), regardless of backtick quoting on either side — quoting
doesn't create a separate, case-sensitive identifier namespace in
BigQuery. A `GROUP BY` item that names an alias the `SELECT` list
assigned to more than one projection is rejected as ambiguous — BigQuery
permits the duplication,
but only as long as nothing references it.

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
