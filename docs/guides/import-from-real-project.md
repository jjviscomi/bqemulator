# Mirror, export, seed: import data into bqemulator

bqemulator ships three CLI subcommands that move catalog state into the
emulator:

- `bqemulator import` — mirror schemas (no row data) from a real
  BigQuery project.
- `bqemulator export` — dump the emulator's catalog and table rows to
  a portable directory.
- `bqemulator seed` — load that directory back into a fresh catalog.

Combined with [`backup` / `restore`](backup-restore.md) (which work on
DuckDB-portable archives) you can shape a local environment that
matches production exactly.

## Mirror from a real project

Install the `import` extra:

```bash
pip install "bqemulator[import]"
```

Run the mirror command, passing the source project id. Credentials come
from Google Application Default Credentials, exactly like
`gcloud auth application-default login`.

```bash
bqemulator import \
    --from-project=my-real-project \
    --dataset=sales --dataset=marketing \
    --target-project=local \
    --data-dir ~/.bqemulator
```

What happens:

1. Connects to BigQuery via the official Python client.
2. Lists datasets in `--from-project` (optionally filtered by
   `--dataset`, which may repeat).
3. For each dataset, mirrors:
 - The `DatasetMeta` (description, labels, location).
 - Every table's schema, partitioning, view query (NO row data).
 - Every routine (UDFs, TVFs, procedures).
4. Writes the metadata into the local persistent catalog at
   `--data-dir/bqemulator.duckdb`.
5. Remaps the project id to `--target-project` (defaults to
   `--from-project`).

After mirroring, start the emulator pointed at the same `--data-dir`
and your queries see the real schemas with empty tables:

```bash
bqemulator start --data-dir ~/.bqemulator
```

Use `bqemulator seed` to populate test rows (see below).

## Export → seed: portable test fixtures

`bqemulator export` writes a directory tree the
[ADR 0020](../adr/0020-admin-import-export.md) format pins:

```
<output_dir>/
    manifest.json
    projects/<project>/
        datasets/<dataset>/
            dataset.json
            tables/<table>.json           # TableMeta
            tables/<table>.parquet        # rows (TABLE only)
            routines/<routine>.json
```

```bash
# Start with a populated local emulator.
bqemulator export \
    --data-dir ~/.bqemulator \
    --output-dir ./seeds/baseline
```

The output is human-readable (JSON schemas) and ecosystem-friendly
(Parquet rows). Commit the directory into version control, ship it
with a test suite, or attach it to a PR for review.

To replay the data into another emulator:

```bash
bqemulator seed \
    --data-dir ~/.bqemulator-ci \
    --input-dir ./seeds/baseline
```

Seed creates missing datasets, tables, and routines; it updates rather
than fails when entries already exist, so re-seeding the same export
twice is safe.

## Round-trip example

```bash
# 1. Mirror schemas from prod.
bqemulator import \
    --from-project=prod-bq \
    --data-dir ~/.bqemu-staging

# 2. Boot the local emulator on the mirrored schemas.
bqemulator start --data-dir ~/.bqemu-staging &

# 3. Insert test rows via the Python client.
python tests/load_fixtures.py

# 4. Snapshot the now-populated catalog.
bqemulator export \
    --data-dir ~/.bqemu-staging \
    --output-dir ./seeds/integration-test

# 5. Commit ./seeds/integration-test into git.

# 6. In CI, restore from the seed.
bqemulator seed \
    --data-dir /tmp/bqemu-ci \
    --input-dir ./seeds/integration-test
bqemulator start --data-dir /tmp/bqemu-ci
```

## Compared to backup / restore

| | `export` + `seed` | `backup` + `restore` |
|---|---|---|
| Output | JSON + Parquet directory | DuckDB-portable directory |
| Readable in git | Yes (JSON, structured) | Partial (Parquet rows are binary) |
| Round-trips routines? | Yes | Yes |
| Round-trips row data? | Yes | Yes |
| Tool compatibility | Any Parquet reader can consume the rows | Any DuckDB version |
| Emulator must be stopped? | Only for export; seed is offline | Yes for both |

Use `export` / `seed` for committed fixtures; use `backup` / `restore`
for ad-hoc snapshots of working state.

## Limitations

- **No row data on import.** The mirror is schema-only by design — real
  BigQuery row reads cost slot-time, and we don't want CI workflows to
  bill production unintentionally. Use `seed` to inject test data.
- **Credentials are ADC-only.** No per-command `--credentials-file`
  flag. Use `gcloud auth application-default login` or
  `GOOGLE_APPLICATION_CREDENTIALS` to provide credentials.
- **PersistenceMode.IMPORT is retired** in [ADR 0020](../adr/0020-admin-import-export.md).
  The enum value remains for backwards compat but no code path reads
  it; use the one-shot `import` command instead.
