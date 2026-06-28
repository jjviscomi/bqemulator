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

- **GitHub Actions pinning.** **Every** `uses:` reference in
  `.github/workflows/*.yml` is pinned to a **full-length commit SHA**
  with a trailing `# vX.Y.Z` comment that names the matching release
  tag — including first-party `actions/*`. SHA pinning is the
  [GitHub Security Lab](https://github.blog/security/supply-chain-security/four-tips-to-keep-your-github-actions-workflows-secure/)
  + [OpenSSF Scorecard](https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies)
  recommendation — major-version tags like `@v4` are mutable and can
  be re-pointed by the action author, which is exactly the supply-
  chain attack vector we're closing. The trailing `# vX.Y.Z` comment
  is Dependabot's canonical bump-anchor; the GHA ecosystem updater
  rewrites both the SHA and the comment together when new releases
  ship, so reproducibility doesn't cost us upgrade hygiene.

  The earlier relaxed rule that exempted first-party `actions/*` from
  SHA-pinning was a pragmatic compromise based on the smaller threat
  model for GitHub-owned actions — but OpenSSF Scorecard's
  `Pinned-Dependencies` check scores full credit only for commit-SHA
  pins regardless of action provenance, and there's no operational
  cost to extending the same rule everywhere (Dependabot handles
  both alike). The exemption was removed as part of the
  Pinned-Dependencies sweep documented in
  [CHANGELOG.md](CHANGELOG.md)'s `[Unreleased]` section.

- **Dockerfile base-image pinning.** `FROM` lines in the
  [`Dockerfile`](Dockerfile) are pinned by digest
  (`python:3.14-slim-bookworm@sha256:…`) in addition to the human-
  readable tag. Same rationale as the Actions pin: tags are mutable;
  digests are immutable. Dependabot's `docker` ecosystem updater
  bumps both the tag and digest together on each upstream release.
- **Python dependency pinning.** The published manifest
  ([`pyproject.toml`](pyproject.toml)) declares **flexible ranges** -- a
  library must not export exact pins to its consumers. Reproducibility
  instead lives in a committed lockfile: [`uv.lock`](uv.lock) is the single
  source of concrete versions. CI installs from it with `uv sync --frozen` and
  `make dev-setup` with `uv sync --locked` (which additionally fails fast if
  the lock has drifted from pyproject), so neither can re-resolve or let an
  upstream release change what a build installs. Changing a dependency is a
  deliberate two-step: edit `pyproject.toml`, run `make lock` to regenerate
  `uv.lock`, and commit both -- the lint gate's `uv lock --check` rejects a
  pyproject edit whose lock was not regenerated. Dependency upgrades arrive
  only as reviewable lockfile PRs (Dependabot's `uv` ecosystem), each running
  the full gate in isolation. A scheduled, non-blocking
  [latest-deps canary](.github/workflows/latest-deps-canary.yml) installs
  latest-within-ranges to surface upstream regressions early. Same rationale
  as the Actions and Docker pins, applied to the last surface that still
  floated. See [ADR 0048](docs/adr/0048-reproducible-builds-lockfile.md).
  (The release build is the deliberate exception: it builds the published
  wheel from the ranges, not the lock, because a library artifact must carry
  ranges.)
- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, `chore:`, `build:`, `ci:`, `perf:`, `style:`). Enforced by
  `commitlint`.
- **Semver**. MAJOR / MINOR / PATCH with a deprecation policy (≥2 minor
  versions or 6 months before removal).
- **Trunk-based branching.** Short-lived `feat/<slug>`, `fix/<slug>`,
  etc. Squash-merge. Signed commits. DCO sign-off.
- **ADRs** for any architectural decision — numbered, immutable, in
  [docs/adr/](docs/adr/).
- **Documentation style.** Docstrings, code comments, and reference
  docs describe the **current state** of the software; the
  [`CHANGELOG.md`](CHANGELOG.md) and [ADRs](docs/adr/) describe
  history. No dates, PR numbers, phase labels, "previously X", or
  TODO/future-work notes in code or reference docs. The CHANGELOG
  follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
  + [Common Changelog](https://common-changelog.org/): one-line
  imperative entries, no per-entry sub-headings, authored at release
  time only. Full rules + examples in
  [`docs/architecture/contributing/documentation-style-guide.md`](docs/architecture/contributing/documentation-style-guide.md).
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

## Pre-commit gate (mandatory)

Run **every** check listed below **before** every commit. CI runs
exactly the same gates and CI failures here are wasted cycles when
they could have been caught in ~90s of local time. Failures are
**not** addressable by pushing-and-watching — fix locally first.

| Command | What it covers | Typical local time |
|---|---|---|
| `make lint` | ruff check + ruff format + mypy --strict + bandit + pip-audit + interrogate + typos | ~30 s |
| `make test-unit` | full unit tier (2400+ tests) | ~45 s |
| `make test-coverage` | combined unit+property+integration with `--cov-fail-under=90` (line + branch). Also writes `coverage.xml` for the patch gate below. | ~3–5 min |
| `make test-patch-coverage` | diff-line coverage on this branch vs `main` (`--fail-under=70`). Mirrors Codecov's `patch` status. Requires a fresh `coverage.xml`, so run **after** `make test-coverage`. | ~5 s |
| Per-example `make test` (when an example was changed) | actual runtime behaviour of the example against a real `bqemulator` start | ~30 s |

Two distinct coverage gates, two thresholds:

* **`make test-coverage` (≥ 90% absolute)** — total project
  line+branch via `--cov-fail-under=90`. The non-negotiable
  release floor. Mirrored in-CI by `Combined U+P+I coverage
  gate (≥90%)`. **This** is the contractual gate.
* **`make test-patch-coverage` (≥ 70% on diff)** — *new lines
  this PR adds* vs `main`. Mirrors Codecov's `patch` status.
  Catches the gap where the project total barely moves but the
  PR's own helpers are uncovered.

Both must pass locally before push.

Codecov's `project` status is configured as `target: auto`
(don't drop below main) with a 0.5% noise threshold — NOT a hard
90% rule. That's deliberate: Codecov aggregates `coverage.xml`
differently than coverage.py's terminal output (typically ~1-2%
lower), so a Codecov-side absolute 90% target trips spuriously
even when local `make test-coverage` is well above 90%. The
absolute floor is enforced *in CI* by the `Combined U+P+I` job
and *locally* by `make test-coverage`; Codecov's role is
regression detection.

When fixing an example or a downstream integration, reproduce the
failure with the example's own `make test` **before** writing any
patch. Do not iterate by pushing to CI and reading logs — that
burns 10× the time and the actual error usually shows up cleaner
under stderr from a local run than it does in a 60 MB CI log
artifact.

## Pre-PR gate (mandatory)

The pre-commit gate above is the per-commit contract. **Before
opening a PR** (or pushing a feature branch you intend to open a PR
from), run the full `make verify` chain — `lint → quality-complexity
→ test (unit + property + integration) → test-coverage → drift checks
→ docker-build → test-e2e (Python + Node + Go + Java + bq CLI) →
docs-build`. ~15-20 min locally.

| Cost | Reason it's worth it |
|---|---|
| ~15-20 min wall-clock locally | A failing CI cycle on PR open is ~20-30 min, and you can't iterate while CI is mid-flight — you wait, fix, push, wait again. Catching the same failure locally saves the cycle. |
| Covers what the pre-commit gate doesn't | `make verify` adds **docker-build** + **test-e2e × 5 clients** + the matrix-drift gates. The pre-commit gate skips these because they're too slow per-commit; per-PR they're table stakes. |
| Catches integration drift CI catches the same way | If a refactor broke the Node client's wire-format expectations, CI surfaces it in test-e2e. The local run surfaces the same failure 15× faster than the push-rebase-wait loop. |

The pre-commit gate stays the per-commit minimum (so multi-commit
branches don't have to pay 20 min per commit). `make verify` is the
"this is ready for review" handoff. CI re-runs the same gates on
every push — so a green `make verify` locally and a CI restart on
the open PR are checking exactly the same surface.

When `make verify` fails locally, **do not open the PR** until the
failure is fixed. Pushing-then-debugging-via-CI is exactly the
anti-pattern the pre-commit-gate section above warns about; the
pre-PR gate extends that contract to the slower checks.

## Review-thread protocol

Every CodeRabbit, CodeQL ("github-advanced-security"), Dependabot,
or human review comment on an open PR follows the same three-step
loop:

1. **Reply on the thread directly** (not as a top-level PR comment)
   via the `POST /repos/.../pulls/{n}/comments/{cid}/replies` API
   (or `gh pr view --comments` + reply through the UI). The reply
   states the resolution: either the fix that's landing, or the
   technical rationale for not acting. Keep it concrete — quote
   the changed file/lines or the CVE rationale.
2. **Land the fix in a commit** that references the thread (the
   commit message should name the file + the warning ID).
3. **Mark the thread resolved** via the GraphQL
   `resolveReviewThread` mutation **after** the commit has pushed
   and the inline reply has been posted. A response without
   resolution leaves the thread open and the PR shows unresolved
   feedback.

Treat "thread is closed" as the only acceptable terminal state.
A reply alone is not enough; a resolved-without-reply thread looks
ignored. Both steps, every time.

## Things to never do

- Commit without **all** of the pre-commit gate above passing
  locally — including both coverage targets:
  `make test-coverage` (project ≥ 90%) **and**
  `make test-patch-coverage` (patch ≥ 70%). The patch target is
  the local mirror of Codecov's `patch` status; running only
  `test-coverage` leaves the patch-coverage blind spot.
- Open a PR without a green `make verify` locally first. The
  pre-commit gate is per-commit; `make verify` is per-PR (covers
  docker-build + test-e2e × 5 clients + matrix-drift gates that
  the per-commit budget skips). Catching a failure in 20 min of
  local CPU beats 20 min of CI + the push-wait-fix loop.
- Commit a new function / class / branch without a unit test
  exercising it the same commit. The project-wide coverage gate
  passes even when diff-introduced lines are uncovered (the
  total just barely moves); the patch gate at 70% is what fails
  fast on that.
- Merge a PR that drops coverage below 90%.
- Merge a feature without e2e coverage for all five conformance clients.
- Push speculative fixes for example / integration regressions
  without first reproducing the failure locally against the
  example's own `make test`.
- Leave a review thread open after a fix lands — every comment
  needs an inline reply **and** a `resolveReviewThread` mutation.
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
