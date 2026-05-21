# Admin + import/export architecture

This page documents the module map and the persistence story
behind the five CLI subcommands and the four `/admin/*` endpoints
that bridge the emulator with real Google Cloud projects.

## Module map

```
bqemulator/
    api/
        admin/
            __init__.py        # build_admin_router()
            jobs.py            # GET /admin/jobs
            catalog.py         # GET /admin/catalog
            streams.py         # GET /admin/streams
            config.py          # GET /admin/config
    commands/
        __init__.py
        import_project.py      # bqemulator import
        export.py              # bqemulator export
        seed.py                # bqemulator seed
        backup.py              # bqemulator backup
        restore.py             # bqemulator restore
```

The `api/admin/` package is imported only when
`Settings.admin_enabled` is True; the import chain stays cold for
production-shaped CI runs.

The `commands/` package is deferred-imported from `cli.py` on a per-
subcommand basis so `bqemulator --version` and `bqemulator start`
remain fast cold-starts.

## Persistence story

The persistent catalog repository
(`bqemulator.catalog.duckdb_repository.DuckDBCatalogRepository`) is a
true write-through implementation backed by the DuckDB tables created
by the catalog migrations (`m001_initial`, `m002_versioning`,
`m003_row_access`):

1. Mutations call the in-memory cache (so uniqueness and not-found
   semantics are enforced) and then write the same row to the
   corresponding DuckDB catalog table.
2. Each entity carries its full Pydantic model serialised in a
   `metadata_json` column for round-trip fidelity, with side-table
   columns (`project_id`, `dataset_id`, `creation_time`, `etag`) that
   support indexed lookups and migrations.
3. `ensure_ready()` runs migrations and hydrates the cache from the
   DuckDB tables; subsequent reads are O(1) cache hits.

This is what makes:

- `bqemulator backup` / `restore` — `EXPORT DATABASE` captures
  meaningful catalog state, not just empty migration tables.
- `bqemulator export` / `seed` — JSON dump of `TableMeta` round-trips
  losslessly via `model_dump_json` + `model_validate_json`.

## Wire formats

### Export directory layout

```
<output>/
    manifest.json
    projects/<project>/datasets/<dataset>/
        dataset.json
        tables/<table>.json
        tables/<table>.parquet            # only for table_type in {TABLE, CLONE}
        routines/<routine>.json
```

`manifest.json` carries `manifestVersion: 1`. `seed` rejects other
versions with a clean `ValueError`.

### Admin endpoint JSON

Each `/admin/*` endpoint returns a top-level `kind` field
(`bqemu#adminJobList`, `bqemu#adminCatalog`, `bqemu#adminStreamList`,
`bqemu#adminConfig`) so clients can route on it. The shapes use
camelCase for parity with BigQuery's REST surface. See
[the admin endpoints guide](../guides/admin-endpoints.md) for full
schemas.

## Storage Write API stream registry

The `WriteStreamManager` is a process-shared instance attached to
`AppContext.write_streams`. The composition root (`server.py`)
constructs it; the gRPC servicer adopts it (and installs the
metric-cleanup callback after the `AppContext` exists); the admin
`/admin/streams` endpoint reads it.

The Storage Read API's `_SESSIONS` module-level dict
(`bqemulator.streaming.read_session`) was kept as a module global —
the gRPC servicer publishes to it from background tasks and the admin
endpoint reads it via a documented snapshot (`tuple(d.values())`),
which avoids a lock that would otherwise have to be held across the
endpoint's response serialisation.

## Composition root changes

```python
# server.py — composition root
write_stream_manager = WriteStreamManager()
context = AppContext(
    settings=...,
    catalog=...,
    write_streams=write_stream_manager,
    ...
)
# gRPC servicer adopts the shared manager and wires the metric callback
# after construction (the callback closes over self._ctx.metrics, which
# only exists once AppContext is built).
write_handler = BigQueryWriteHandler(context)  # internally adopts ctx.write_streams
```

The `WriteStreamManager.set_on_remove` setter is the seam that lets
the servicer wire its metric-cleanup callback post-construction.
