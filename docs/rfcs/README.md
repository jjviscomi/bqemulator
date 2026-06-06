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
