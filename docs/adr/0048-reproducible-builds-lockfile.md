# ADR 0048: Reproducible builds via a committed lockfile, flexible library ranges, and a latest-deps canary

- **Status**: Accepted

## Context

`bqemulator` declares every Python dependency, runtime and tooling alike, as
a floor-only range (`sqlglot>=30.9`, `pytest>=9.0.3`, `ruff>=0.3`, and so on),
with a handful of reactive ceilings added after past breakages
(`grpcio-tools<1.81`, `fastapi!=0.136.3`, `sqlglot<30.12`). There is **no
committed lockfile** (`uv.lock`, `poetry.lock`, `requirements*.txt`, or
`constraints*.txt`), and CI installs with `pip install -e ".[dev,all]"` on
every run, which resolves to the **latest** compatible release of every
package each time.

The consequence is that `main`'s green/red status depends on what PyPI
published recently rather than on the repository's own code. A new upstream
release can fail CI with zero local changes, and the failure surfaces on
whatever PR happens to run next:

- **sqlglot 30.12.0** (the incident that prompted this ADR, fixed tactically
  in #189) regressed two BigQuery-to-DuckDB codegen paths and reddened CI on
  an unrelated Dependabot batch PR. Re-running `main`'s own CI on an unchanged
  commit reproduced the failure, confirming the cause was upstream drift, not
  the PR.
- Prior episodes followed the same pattern: the sqlglot 30.9.0 break, pytest
  minor bumps, and the starlette CVE line (see [ADR 0044](0044-starlette-1x-adoption.md)).

This is not a gap in diligence; it is the predictable behaviour of an
unlocked, float-to-latest install. Notably, the project **already** pins its
supply-chain-sensitive surfaces for exactly this reason: every GitHub Actions
`uses:` is pinned to a full commit SHA and every `Dockerfile` base image is
pinned by digest, both maintained by Dependabot (see `AGENTS.md`). Python
dependency resolution is the one remaining surface left to float.

The complicating constraint is that `bqemulator` is a **published library**
(PyPI), not an application. Exact-pinning runtime dependencies in
`[project.dependencies]` would not buy reliability; it would *export*
dependency conflicts to every downstream project that installs `bqemulator`
alongside other packages. The reliability we want belongs at the build layer,
not in the published contract.

The standard resolution is to separate **abstract** dependencies (what the
library promises the world) from **concrete** dependencies (what this
repository actually builds and tests against).

## Decisions

### 1. Keep `[project.dependencies]` as flexible ranges

The published manifest continues to declare floors (and only the reactive
ceilings that document a known-incompatible release). Runtime dependencies are
**not** exact-pinned. This preserves correctness for downstream consumers and
keeps `bqemulator` a well-behaved library in a shared dependency graph. The
existing ceilings (`grpcio-tools<1.81`, `fastapi!=0.136.3`, `sqlglot<30.12`)
remain as a documented safety net, each carrying its rationale in a comment.

### 2. Commit a lockfile and install from it in CI and dev

A committed lockfile becomes the single source of concrete versions for the
repository's own builds. CI installs from the lock with a frozen / no-resolve
flag so an upstream release can never silently change what CI runs. The lock
covers tooling (ruff, mypy, pytest, xenon, and the rest) as well as runtime
and test dependencies, so linter and test-runner upgrades that change rules or
warnings-as-errors behaviour become deliberate, reviewed changes rather than
spontaneous failures.

**Mechanism: `uv` with `uv.lock`.** `uv` produces a hashed, cross-platform
lockfile, is fast, is supported by Dependabot's `uv` ecosystem updater, and
composes with the existing `pyproject.toml` / `.venv` model (`uv sync` creates
and populates `.venv`, which the Makefile already binds to). CI provisions the
interpreter and `uv` via `astral-sh/setup-uv` (SHA-pinned like every other
action) and installs with `uv sync --frozen`, so the resolution is taken from
the lock and never recomputed mid-pipeline. Two alternatives were considered
and rejected: `pip-tools` (a hashed `constraints.txt` installed with
`pip install -c`) keeps pure `pip` but is slower, less ergonomic, and has no
single-command sync; `poetry` would restructure the dependency tables and is
the most disruptive. `uv` is the decision.

### 3. Add a scheduled latest-deps canary that is allowed to fail

A separate scheduled workflow installs the dependencies fully unpinned (latest
of everything, ignoring the lock) and runs the test suite. It is **non-blocking**:
it does not gate any PR, and on failure it surfaces a clear signal (a job
failure and/or an auto-filed issue). This preserves the one genuine benefit of
the current float-to-latest setup, early detection of upstream regressions,
without gambling the `main` branch on it. The sqlglot 30.12.0 break would have
appeared here, on a schedule, with an unambiguous cause, instead of by surprise
on unrelated work.

### 4. Dependabot maintains the lockfile

Dependabot's `uv` (or `pip`) ecosystem updater keeps the lock current,
continuing alongside the existing `github-actions` and `docker` updaters. A
dependency upgrade therefore arrives as a reviewable lockfile PR that runs the
full CI gate **in isolation**, which is precisely where a regression like
sqlglot 30.12.0 should be caught and contained.

## Consequences

- `main` can no longer turn red because of an upstream release with no code
  change; per-PR CI becomes reproducible.
- Contributors get a deterministic `make dev-setup` that matches CI exactly.
- Dependency and tooling upgrades become explicit, reviewed lockfile PRs
  instead of ambient drift; a bad release is caught on its own PR.
- The latest-deps canary keeps the project ahead of upstream changes as a
  non-blocking early-warning signal.
- Downstream consumers are unaffected: the published ranges do not change, so
  `bqemulator` keeps resolving flexibly in a shared environment.
- Costs: a lockfile to maintain (mitigated by Dependabot), one additional tool
  (`uv`) in the toolchain, and slightly more ceremony per upgrade (a lockfile
  PR rather than an implicit resolve). These are the same trade-offs the
  project already accepted for SHA-pinned Actions and digest-pinned images.
- The implementation (adopting the lock tooling, wiring CI to install frozen,
  the latest-deps canary workflow, `dependabot.yml`, the Makefile, and the
  contributor docs) lands together with this ADR as a single self-contained
  change. It touches build and CI configuration only, not the library's
  runtime behaviour, so it carries no feature risk.
