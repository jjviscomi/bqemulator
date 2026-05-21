# Local file load (G2 upload host)

Demonstrates `client.load_table_from_file(io.BytesIO(...))` against
the emulator's `/upload/bigquery/v2/...` endpoints.

The script:

1. Starts an ephemeral emulator on a random port via the
   `bqemulator.testing.testcontainers` helper (or any existing
   running instance via `BIGQUERY_EMULATOR_HOST`).
2. Creates a dataset and a `customers (id INT64, name STRING)` table.
3. Uploads a 3-row CSV via the Python client's multipart upload path.
4. Queries the rows back and asserts the count is 3.

## Run

```bash
make test
```

## What to look for

- The same `LoadJobConfig` flags work as against real BigQuery.
- The `load_table_from_file` call returns a normal `LoadJob` object;
  `job.result()` blocks until the load completes.
- The upload host routes (`/upload/bigquery/v2/...`) are completely
  transparent to the client — they're an implementation detail of the
  `google-cloud-bigquery` library.
