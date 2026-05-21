# ADR 0020: Admin endpoints, import/export/seed, and offline backup format

- **Status**: Accepted

## Context

Phase 10 ships five new CLI subcommands (`import`, `export`, `seed`,
`backup`, `restore`) and four `/admin/*` HTTP endpoints (jobs, catalog,
streams, config). Each new surface introduces a design question:

1. Where do admin endpoints live in the threat model, and how do callers
   opt in?
2. What file layout does `export` produce, and how does `seed` read it
   back?
3. How does `backup` capture the persistent DuckDB database in a way
   that `restore` can reverse without divergence?
4. How does `import` reconcile schemas pulled from a real BigQuery
   project against the local catalog?

The Phase 0 placeholder catalog (Pydantic models held in memory) was
*not* persistent — DuckDB migration tables existed but the repository
never wrote to them. Phase 10 needs round-trippable persistence to ship
backup/restore and seed/export honestly. The promotion of
`DuckDBCatalogRepository` from in-memory to true write-through is part
of this ADR.

## Decisions

### 1. `/admin/*` endpoints are opt-in via `Settings.admin_enabled`

The admin router (`bqemulator.api.admin.build_admin_router`) is wired
into the FastAPI app only when `settings.admin_enabled` is True. The
default is False; the published Docker image inherits that default; the
test-container wrapper sets `BQEMU_ADMIN_ENABLED=1` so the E2E suites
exercise the surface against a real container.

Options considered:

1. **Opt-in flag (selected).** Matches BigQuery's "trust the local
   environment" stance; mirrors how `docs` / `openapi.json` are gated
   on the same flag today; one configuration knob covers every
   diagnostic endpoint.
2. **Always-on routes with separate per-endpoint flags.** Reject — more
   knobs, more documentation; admin surface area is already small.
3. **Authentication on the admin surface.** Reject — the emulator
   doesn't authenticate any other endpoint, and adding a token gate
   here would create a misleading half-secure surface. See ADR 0018 for
   the broader "IAM out of scope" decision.

The admin endpoints are read-only. They render summaries, not full
object dumps, so introducing a row leak through the admin surface is
not practical even with the flag enabled.

### 2. Export format: JSON for schemas, Parquet for rows

Options considered:

1. **YAML schemas + Parquet rows.** Reject — YAML adds a runtime
   dependency, complicates round-trip equality (JSON vs YAML key
   ordering, anchors), and doesn't buy human-readability that
   `json.dumps(..., indent=2, sort_keys=True)` doesn't already deliver.
2. **JSON schemas + Parquet rows (selected).** Stdlib `json`. Pydantic
   models round-trip losslessly via `model_dump_json(by_alias=True)` →
   `model_validate_json`. Parquet via DuckDB's `COPY... TO` is fast
   and ecosystem-friendly.
3. **A monolithic SQLite/DuckDB file.** Reject — opaque to git diff,
   loses the "per-table Parquet" property that lets external tools
   (Apache Arrow, pandas) consume seed data directly.

The directory layout is locked here so seed/export can guarantee
round-tripping:

```
<output_dir>/
    manifest.json                              # version + counts
    projects/<project>/datasets/<dataset>/
        dataset.json
        tables/<table>.json                    # TableMeta
        tables/<table>.parquet                 # rows (TABLE only)
        routines/<routine>.json
```

`manifest.json` carries an integer `manifestVersion` (`1` at ship).
Future format changes bump that number and `seed` refuses incompatible
versions with a clean error.

### 3. Backup uses DuckDB EXPORT DATABASE; runs offline

Options considered:

1. **Tarball of the.duckdb file.** Reject — DuckDB's wire format is
   not guaranteed stable across versions, and a binary archive obscures
   what's in the backup.
2. **`EXPORT DATABASE` to a directory (selected).** DuckDB's documented
   portable format: a `schema.sql` plus per-table Parquet. Reverse via
   `IMPORT DATABASE`. Works across DuckDB versions; works for any
   future on-disk format change.
3. **Live online backup via a running emulator.** Reject — adds a
   network surface (POST to a diagnostic endpoint), needs WAL/PIT
   semantics we don't have, and requires the emulator to be running
   when the user calls `backup`. The offline model means "stop server →
   backup" is a clean, dependency-free workflow.

`backup` and `restore` talk to DuckDB directly via `duckdb.connect()`
rather than through `DuckDBEngine.start()`. The engine's startup
creates the catalog schemas unconditionally; that conflicts with the
`CREATE SCHEMA` statements replayed by `IMPORT DATABASE`. Bypassing the
engine avoids a `Schema already exists` error without adding a special
"restore mode" code path to the engine.

Both commands attempt to load DuckDB's spatial extension so any
GEOGRAPHY columns round-trip; failure is non-fatal and logged.

### 4. Import is a one-shot CLI command (not a persistence mode)

The existing `PersistenceMode.IMPORT` enum value is **retired** by this
ADR. Originally proposed as a "live import" mode where the server
periodically syncs schemas from a real project, that design overlaps
with `bqemulator import --from-project=…`, doubles the credential
surface, and adds an ongoing dependency on the BigQuery REST API that's
incompatible with offline test environments. The cleanest pattern is:

- One-time import: `bqemulator import --from-project=real --data-dir=…`
- Local-only afterwards: start the server pointed at the same data_dir.

The enum value remains in `bqemulator.config.PersistenceMode` for
backwards compatibility (no field references it; no behaviour changes
on `mode=IMPORT`), but no Phase 10 code path treats it specially.

### 5. Catalog write-through is mandatory for persistent mode

Until Phase 10, `DuckDBCatalogRepository` kept all metadata in memory.
Persistent mode persisted *data* tables but not the catalog rows
describing them, so a process restart lost every dataset / table /
routine even though the underlying DuckDB had the row data. Phase 10
fixes this: every mutation writes through to the DuckDB catalog tables,
and `ensure_ready()` hydrates the in-memory cache from those rows.

This unblocks:

- `backup` / `restore` round-tripping the catalog (was impossible
  before because the catalog tables were empty).
- `seed` writing into a `data_dir` and then having a fresh emulator
  read it back.
- A previously latent bug where Phase 0+ "persistent mode" was actually
  ephemeral for everything except table rows.

Implementation: the `_cache: MemoryCatalogRepository` stays as the
fast-read source; every mutation calls the cache (which enforces
uniqueness / not-found semantics) and then writes the same row to the
backing DuckDB table. Hydration runs after migrations.

### 6. Admin endpoint JSON shapes

Verified against real BigQuery's REST shapes only where applicable
(catalog entities use BigQuery's camelCase keys for round-tripping with
existing routes). The `/admin/*` endpoints are emulator-only — they have
no real-BigQuery analogue — and use `kind: "bqemu#admin*"` markers so
clients can detect them. The endpoint summaries omit row data; only
metadata fields are returned.

## Consequences

- **Positive.** All five CLI commands round-trip. Persistent mode is
  truly persistent. Admin endpoints give CI users a debug surface
  without a custom server.
- **Positive.** The catalog refactor closes a latent Phase 0 bug. No
  Phase 1–9 test regressed because reads still came from the cache;
  writes were just additionally durable.
- **Negative.** Catalog mutations now incur one extra DuckDB write per
  call. For the emulator's workload (hundreds of catalog writes per
  test run) this is invisible; for any future hot-path catalog churn
  we'd want to batch.
- **Negative.** Backup/restore require an offline emulator. CI flows
  that want a "no-stop" backup must rely on filesystem snapshots
  (LVM / btrfs / Docker volume snapshot) — out of scope for v1.0.0.
- **Negative.** `PersistenceMode.IMPORT` is retired. Documented but
  not actionable. Removed in v2 via a deprecation ADR.
