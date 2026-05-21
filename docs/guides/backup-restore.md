# Backup and restore

`bqemulator backup` and `bqemulator restore` round-trip the persistent
DuckDB database (catalog rows + table rows) via DuckDB's own
`EXPORT DATABASE` / `IMPORT DATABASE` machinery. Both commands run
**offline** — the emulator must be stopped while they execute, because
DuckDB holds an exclusive write lock on the database file.

## When to use

- **CI**: stop the emulator → `backup` → archive the directory → restart.
- **Local dev**: snapshot a working state before a destructive
  experiment, restore if it goes wrong.
- **Cross-machine seed**: produce a backup on machine A, ship it to
  machine B, restore + start.

Use [`bqemulator export`](import-from-real-project.md) instead if you
want a human-readable JSON + Parquet dump for committing into version
control.

## Backup

```bash
bqemulator backup \
    --data-dir ~/.bqemu \
    --to /tmp/bqemu-backup-2024-05-14
```

The destination directory must be empty or non-existent. The command:

1. Opens `~/.bqemu/bqemulator.duckdb` (read-write so the spatial
   extension can be loaded).
2. Runs DuckDB's `EXPORT DATABASE '<dir>' (FORMAT PARQUET)`, producing
   a `schema.sql` plus per-table Parquet files.
3. Closes the connection.

The backup directory is a DuckDB-portable bundle. Any tool that can
read Parquet plus replay the SQL in `schema.sql` can use it.

## Restore

```bash
bqemulator restore \
    --data-dir ~/.bqemu-restored \
    --from /tmp/bqemu-backup-2024-05-14 \
    --force
```

The command:

1. Refuses to overwrite an existing `bqemulator.duckdb` unless
   `--force` is passed.
2. Creates the destination directory if needed.
3. Opens a fresh DuckDB connection on the new database.
4. Runs `IMPORT DATABASE '<dir>'` to materialise the schema and table
   rows.

After restore, start the emulator pointed at the restored `--data-dir`:

```bash
bqemulator start --data-dir ~/.bqemu-restored
```

## Common errors

| Symptom | Likely cause |
|---|---|
| `Not a bqemulator backup directory: …` | `--from` doesn't contain a `schema.sql`. Either the wrong directory or an incomplete backup. |
| `Destination already exists (pass --force to overwrite): …` | `--data-dir` already has a `bqemulator.duckdb`. Pass `--force` to replace it. |
| `Could not bind to database` | The source emulator is still running. Stop it before backup. |

## What's NOT in the backup

- **In-memory write streams** — `BigQueryWrite` streams (PENDING and
  BUFFERED states) live only in process memory; a restart drops them.
  See [`docs/reference/out-of-scope.md`](../reference/out-of-scope.md).
- **Read sessions** — Storage Read API sessions are likewise in-memory.
- **Job result blobs** — query results held in the in-memory cache are
  not part of the persistent state.

## Online backup is out of scope for v1.0.0

The emulator does not support backing up while running. ADR 0020 has
the rationale: an online backup would require either a network endpoint
that triggers `EXPORT DATABASE` (adding a new admin write surface) or a
WAL-aware filesystem snapshot integration. Both are projects of greater
scope than v1.0.0's "integration-test target" charter.

For users who need an online snapshot, run the emulator inside a
filesystem with copy-on-write semantics (btrfs / ZFS / Docker volume
snapshot) and snapshot the underlying volume instead.
