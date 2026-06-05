# Export to GCS (`EXPORT DATA`)

Demonstrates the GoogleSQL `EXPORT DATA OPTIONS(...) AS SELECT` statement
against the emulator, writing query results to a Cloud Storage URI as
CSV.

`gs://` URIs resolve under `BQEMU_GCS_LOCAL_ROOT`, so the script starts an
in-process emulator rooted at a temporary directory, runs the export, and
reads the exported file straight off that directory.

The script:

1. Starts an ephemeral emulator on a random port with
   `BQEMU_GCS_LOCAL_ROOT` pointed at a temp directory.
2. Creates a dataset and a `customers (id INT64, name STRING)` table and
   inserts three rows.
3. Runs `EXPORT DATA OPTIONS(uri='gs://my-bucket/exports/customers_*.csv',
   format='CSV', overwrite=true) AS SELECT ... ORDER BY id` as a query
   job and waits for it to finish.
4. Asserts the job's `statement_type` is `EXPORT_DATA`, then reads the
   single shard `customers_000000000000.csv` off the GCS root and checks
   the header and rows.

## Run

```bash
make test
```

`make test` runs `python example.py`. In CI the repo is installed in
editable mode (`pip install -e ".[dev,all]"`), which provides both
`bqemulator` and the `google-cloud-bigquery` client; `make install`
installs the pinned client from `requirements.txt` for a standalone run.

## What to look for

- The export runs as an ordinary **query job** — `client.query(...)` —
  not a load/extract job. `job.statement_type` is `EXPORT_DATA` and the
  result has zero rows.
- The single `*` wildcard expands to a 12-digit counter, so a small
  result is one file named `customers_000000000000.csv`.
- `gs://my-bucket/exports/customers_000000000000.csv` resolves to
  `$BQEMU_GCS_LOCAL_ROOT/my-bucket/exports/customers_000000000000.csv` —
  the export creates the parent directories automatically.
- CSV defaults to `header=true`, so the first line is `id,name`.

See the [Exporting data guide](../../../guides/exporting-data.md) for the
full OPTIONS reference, sharding behaviour, and limitations.
