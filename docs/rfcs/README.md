# RFCs

Design changes that affect the public API, SQL semantics, persistence
format, or governance go through an RFC before implementation.

## Lifecycle

1. Copy `.github/rfc-template.md` into `docs/rfcs/NNNN-slug.md` (next
   free number).
2. Open a PR with status `Draft`.
3. Move to `Review` once feedback settles; review runs for ≥2 weeks.
4. The TSC accepts, rejects, or defers by consensus (majority vote if
   consensus fails).
5. Accepted RFCs drive implementation PRs; the outcome is summarized in
   an ADR in `docs/adr/`.

### Maintainer fast-track

When a maintainer has already ratified a design before drafting (so the
≥2-week review window would add no signal), the RFC may be accepted on a
fast-track: it is authored with status `Accepted`, and implementation
proceeds in the same phased PR series rather than waiting on a separate
review cycle. A fast-tracked RFC says so in a note under its title and
still records its implementation outcome in an ADR. This path is for
maintainer-driven work with a pre-settled design; community-proposed RFCs
follow the full lifecycle above.

## When to open an RFC

- New public REST or gRPC endpoints
- Changes to SQL semantics (including new rules that aren't pure
  BigQuery → DuckDB translations)
- Changes to the catalog / persistence format
- New runtimes (additional UDF language, new storage backend)
- Governance / process changes

See the [CONTRIBUTING guide](https://github.com/jjviscomi/bqemulator/blob/main/CONTRIBUTING.md)
for the PR mechanics.

## Active RFCs

| RFC | Title | Status |
|---|---|---|
| [0001](0001-export-data-statement.md) | EXPORT DATA statement (Cloud Storage) | Accepted |
| [0002](0002-bigquery-ml-surface.md) | BigQuery ML surface (metadata, Models REST, ML.PREDICT shape) | Accepted |
