# Loading data

Status: shipped.

Load jobs accept CSV, JSON, Parquet, Avro, and ORC, with gzip, zstd,
and snappy compression. Source URIs may be local file paths or `gs://…`
URIs (resolved under `BQEMU_GCS_LOCAL_ROOT`). `writeDisposition` supports
`WRITE_APPEND`, `WRITE_TRUNCATE`, `WRITE_EMPTY`.

```python
job_config = bigquery.LoadJobConfig(
    source_format=bigquery.SourceFormat.PARQUET,
    write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
)
load_job = client.load_table_from_uri(
    "file:///tmp/orders.parquet",
    "my-project.sales.orders",
    job_config=job_config,
)
load_job.result()
```

Tracking:
[issue](https://github.com/jjviscomi/bqemulator/issues?q=is%3Aissue+load-jobs).
