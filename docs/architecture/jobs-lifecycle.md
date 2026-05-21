# Jobs lifecycle

Every async operation (query, load, extract, copy, snapshot) is a **Job**.

## State machine

```
    ┌─────────┐   insert   ┌─────────┐    start    ┌──────┐
    │ (none)  │───────────▶│ PENDING │────────────▶│ DONE │
    └─────────┘            └─────────┘             └──────┘
                               │
                               │   start
                               ▼
                           ┌─────────┐   complete   ┌──────┐
                           │ RUNNING │─────────────▶│ DONE │
                           └─────────┘              └──────┘
                               │                       ▲
                               │   cancel              │
                               └───────────────────────┘
```

Transitions are validated by `bqemulator.jobs.state_machine.advance()`;
invalid transitions raise `InternalError`.

## Command pattern

Each job type is a `*JobCommand` in `bqemulator.jobs.commands/`:

- `QueryJobCommand` — SQL translation + DuckDB execute + Arrow result
  materialization
- `LoadJobCommand` — DuckDB `COPY FROM` for CSV/JSON/Parquet/Avro/ORC
- `ExtractJobCommand` — DuckDB `COPY TO`
- `CopyJobCommand` — `INSERT INTO … SELECT FROM`
- `SnapshotJobCommand` — snapshot layer capture

All share the signature `async execute(ctx: JobContext) -> JobResult`.

## Pagination

Query results are held as `pyarrow.Table` on the `JobState`.
`getQueryResults` slices the table by `startIndex` / `maxResults` and
converts via `bqemulator.storage.arrow_bridge`.

## Dry run

`configuration.dryRun=true` runs the SQL translation but not the
execution. `totalBytesProcessed` is computed by summing `num_bytes` of
referenced tables from the catalog.

## Cache

Identical, deterministic queries return cached results within the
configured TTL (`BQEMU_QUERY_CACHE_TTL_SECONDS`). Cache entries are
invalidated by `TableDataChanged` events for dependent tables.
