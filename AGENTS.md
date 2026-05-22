# bqemulator — orientation for AI assistants and new contributors

Quick orientation file for AI coding assistants and any human cracking
the repo open for the first time. Pairs with
[docs/architecture/overview.md](docs/architecture/overview.md), the
canonical architectural reference.

## What this project is

Open-source local emulator for Google BigQuery. Python 3.11+, DuckDB-backed,
SQLGlot-powered. Drop-in replacement for the real service for dev, CI, and
offline replicas. Apache 2.0.

## Non-negotiable principles

1. **Highest engineering standards.** Hexagonal architecture, dependency
   injection, strict types, pattern-driven (Strategy, Command, Repository,
   Visitor, Interpreter). No shortcuts.
2. **≥90% line + branch coverage.** CI fails below threshold. No exceptions.
3. **E2E against live Docker containers.** Every scenario passes against
   `ghcr.io/jjviscomi/bqemulator:dev` for all five conformance clients
   (Python, Node.js, Go, Java SDKs + Google's `bq` CLI) before merge.
4. **Comprehensive docs with runnable examples.** Every user-facing feature
   has a guide AND a runnable CI-verified example. `mkdocs build --strict`
   in CI.
5. **No deferral.** When starting a feature, complete it. Scope boundaries
   are explicit exclusions with documented rationale — never "TODO for
   v1.1". See [`docs/reference/out-of-scope.md`](docs/reference/out-of-scope.md).

## Quick commands

| Command | What it does |
|---|---|
| `make dev-setup` | Install deps + pre-commit hooks |
| `make verify` | Full release-ready gate chain (lint → unit → property → integration → docker → e2e → docs) |
| `make lint` | ruff + format + mypy --strict + bandit + pip-audit + interrogate + typos |
| `make test-unit` | Unit tests, <10s |
| `make test-property` | Hypothesis property tests |
| `make test-integration` | In-process emulator + Python client |
| `make docker-build` | Build multi-arch image `ghcr.io/jjviscomi/bqemulator:dev` |
| `make test-e2e` | Live container + all five conformance clients |
| `make test-conformance` | Replay corpus against in-process emulator (offline) |
| `make record-conformance` | Re-record corpus baselines from real BigQuery (requires `GOOGLE_APPLICATION_CREDENTIALS` + `BQEMU_CONFORMANCE_PROJECT`) |
| `make coverage-matrix` | Regenerate `docs/reference/conformance-coverage-matrix.md` |
| `make test-perf` | pytest-benchmark, regressions >10% fail |
| `make docs-serve` | Local MkDocs preview |
| `make docs-build` | `mkdocs build --strict` |
| `make release-dry-run NEXT=minor` | Preview a release |
| `make release NEXT=minor` | Apply a release (verify + bump + changelog + commit + tag) |
| `bqemulator start --ephemeral` | Start emulator for manual debugging |
| `bqemulator start --data-dir /tmp/bqemu` | Persistent mode |

## Architecture (hexagonal)

```
api/  ──┐
        ├── domain/ + catalog/ + storage/ + sql/ + jobs/ + streaming/
grpc_api/ ──┘        + scripting/ + udf/ + versioning/ + types/
                     + row_access/ + views/ + commands/
```

- `src/bqemulator/domain/` — framework-free pure domain (errors, `Result`,
  clock, IDs, events).
- `src/bqemulator/catalog/` — metadata, Repository pattern, in-memory +
  DuckDB-backed implementations.
- `src/bqemulator/storage/` — DuckDB engine + type mapping + Arrow bridge
  + partition state.
- `src/bqemulator/sql/` — SQLGlot orchestrator + rule strategies +
  rewriters + query cache + built-in UDFs.
- `src/bqemulator/scripting/` — BigQuery scripting interpreter
  (`DECLARE` / `BEGIN` / `END` / `IF` / `LOOP` / `EXCEPTION` +
  `BEGIN`/`COMMIT`/`ROLLBACK` transaction shim per
  [ADR 0015](docs/adr/0015-scripting-execution-model.md)).
- `src/bqemulator/udf/` — SQL / JS (V8) / TVF runtimes.
- `src/bqemulator/versioning/` — snapshots, time-travel, clones,
  materialized views.
- `src/bqemulator/jobs/` — command-pattern executor for query / load /
  extract / copy / snapshot.
- `src/bqemulator/streaming/` — Storage Read (Arrow + Avro) + Storage
  Write APIs (strategy per stream type) + proto / Arrow row
  deserialisers.
- `src/bqemulator/row_access/` — RAP enforcement via rewriter.
- `src/bqemulator/views/` — authorized views.
- `src/bqemulator/types/` — `GEOGRAPHY`, `RANGE`, `INTERVAL`, numeric,
  timestamp.
- `src/bqemulator/commands/` — CLI subcommands (`start`, `import`,
  `admin`, …) routed by `cli.py`.
- `src/bqemulator/api/` — FastAPI REST adapter (incl. multipart +
  resumable upload routes per
  [ADR 0029](docs/adr/0029-upload-host-endpoints.md)).
- `src/bqemulator/grpc_api/` — `grpc.aio` adapter (Storage Read + Storage
  Write servicers).
- `src/bqemulator/observability/` — `structlog`, OpenTelemetry, Prometheus.
- `src/bqemulator/testing/` — testcontainers helpers + pytest plugin
  entry points.

## Conventions

- **GitHub Actions pinning.** Third-party `uses:` references in
  `.github/workflows/*.yml` are pinned to a **full-length commit SHA**
  with a trailing `# vX.Y.Z` comment that names the matching release
  tag. SHA pinning is the
  [GitHub Security Lab](https://github.blog/security/supply-chain-security/four-tips-to-keep-your-github-actions-workflows-secure/)
  + [OpenSSF Scorecard](https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies)
  recommendation — major-version tags like `@v1` are mutable and can
  be re-pointed by the action author, which is exactly the supply-
  chain attack vector we're closing. The trailing `# vX.Y.Z` comment
  is Dependabot's canonical bump-anchor; the GHA ecosystem updater
  rewrites both the SHA and the comment together when new releases
  ship, so reproducibility doesn't cost us upgrade hygiene. First-
  party `actions/*` (GitHub-owned) are exempt and may use major-
  version tags (`actions/checkout@v4`) — GitHub's own actions live
  under a different threat model.
- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, `chore:`, `build:`, `ci:`, `perf:`, `style:`). Enforced by
  `commitlint`.
- **Semver**. MAJOR / MINOR / PATCH with a deprecation policy (≥2 minor
  versions or 6 months before removal).
- **Trunk-based branching.** Short-lived `feat/<slug>`, `fix/<slug>`,
  etc. Squash-merge. Signed commits. DCO sign-off.
- **ADRs** for any architectural decision — numbered, immutable, in
  [docs/adr/](docs/adr/).
- **RFCs** for any change to public API, SQL semantics, persistence
  format, or governance — in [docs/rfcs/](docs/rfcs/).
- **PR template** required. Checklist: tests, docs, changelog,
  ADR-if-needed, migration-if-needed.
- **CODEOWNERS** routes review. Minimum 2 approvals (or 1 reviewer +
  maintainer for trivial changes).

## Testing expectations

- Every public function has a docstring (`interrogate` enforces ≥90%).
- Every new feature: unit test(s) + e2e test(s) against live container
  + doc update + changelog entry.
- Every new SQL rule: unit test + conformance test case.
- Combinatorial surface → property test with Hypothesis.
- Never mock DuckDB in integration tests; use real DuckDB.
- Never skip an e2e test language — all five conformance clients
  (Python / Node.js / Go / Java SDKs + `bq` CLI) must exercise every
  scenario.
- **Every new BigQuery surface item gets a `SurfaceItem` entry** in
  [`tests/conformance/_surface_inventory.py`](tests/conformance/_surface_inventory.py)
  so the
  [conformance coverage matrix](docs/reference/conformance-coverage-matrix.md)
  tracks fixture depth. `make verify` calls `coverage-matrix-check`
  which fails if the inventory has grown without regenerating the
  matrix, or vice versa.

## Documentation expectations

- Every user-facing feature has a guide in `docs/guides/`.
- Every non-trivial decision has an ADR.
- Every scope-boundary exclusion is listed in
  [`docs/reference/out-of-scope.md`](docs/reference/out-of-scope.md)
  with rationale.
- Every example in `docs/examples/` has its own `make test` run in CI.
- Reference docs auto-generated from tests / docstrings wherever
  possible.

## Release process

Backed by three scripts under [`scripts/`](scripts/) — see
[`docs/architecture/contributing/release-process.md`](docs/architecture/contributing/release-process.md)
for the full operator-facing flow and per-script exit-code reference.

```bash
# Preview
make release-dry-run NEXT=minor

# Apply (runs verify + bump + changelog + commit + annotated tag)
make release NEXT=minor

# Push (release.yml + docker.yml fire on the tag)
git push origin main vX.Y.Z
```

## Things to never do

- Commit without `make lint test-unit` passing locally.
- Merge a PR that drops coverage below 90%.
- Merge a feature without e2e coverage for all five conformance clients.
- Add `TODO` or `FIXME` without a linked issue number.
- Mock DuckDB in integration tests.
- Defer scope to "v1.1" or "later" — complete in-phase or exclude
  cleanly with rationale.
- Ship an undocumented public API.
- Bypass the PR workflow on `main` (main is protected, signed,
  squash-merge).
- Skip an ADR for an architectural decision.
- Remove a deprecated API before the deprecation window elapses.

## Where to look first

| Task | Start here |
|---|---|
| New to the project | [`docs/architecture/overview.md`](docs/architecture/overview.md) + ADRs 0001–0012 |
| Implementing a SQL feature | [`docs/architecture/contributing/adding-sql-functions.md`](docs/architecture/contributing/adding-sql-functions.md) |
| Implementing a Storage API feature | [`docs/architecture/storage-read-api.md`](docs/architecture/storage-read-api.md) / [`storage-write-api.md`](docs/architecture/storage-write-api.md) |
| Adding a conformance case | [`docs/architecture/contributing/adding-conformance-cases.md`](docs/architecture/contributing/adding-conformance-cases.md) |
| Hitting a test failure | [`docs/architecture/contributing/debugging.md`](docs/architecture/contributing/debugging.md) |
| Shipping a release | [`docs/architecture/contributing/release-process.md`](docs/architecture/contributing/release-process.md) |
| Scope question ("should we build X?") | [`docs/reference/out-of-scope.md`](docs/reference/out-of-scope.md) + open an RFC |
| Picking which fixtures to author next | [`docs/reference/conformance-coverage-matrix.md`](docs/reference/conformance-coverage-matrix.md) — surface-by-surface fixture-count breakdown with gap callouts |
| Adding a new BigQuery surface to track | [`tests/conformance/_surface_inventory.py`](tests/conformance/_surface_inventory.py) — append a `SurfaceItem`, then `make coverage-matrix` |
