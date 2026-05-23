# Contributing to bqemulator

Welcome, and thank you for contributing. This guide covers the mechanics
of getting a change merged. For the technical architecture, start with
[docs/architecture/overview.md](docs/architecture/overview.md) and
[AGENTS.md](AGENTS.md).

## Your first contribution

If you're new to the project, start here:

1. **Browse open issues** — look for ones tagged
   [`good first issue`](https://github.com/jjviscomi/bqemulator/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
   or [`help wanted`](https://github.com/jjviscomi/bqemulator/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22).
2. **Pick a fixture-authoring task** — the
   [conformance coverage matrix](https://jjviscomi.github.io/bqemulator/latest/reference/conformance-coverage-matrix/)
   lists every BigQuery surface item with a current fixture count. Items
   tagged 🔴 (uncovered) or 🟡 (sampled) are concrete, well-scoped
   contribution opportunities — see
   [adding conformance cases](https://jjviscomi.github.io/bqemulator/latest/architecture/contributing/adding-conformance-cases/).
3. **Try an example project** — running any example in
   [`docs/examples/`](docs/examples/) end-to-end is a useful smoke test
   that also surfaces any rough edges worth filing.
4. **Ask for help** in [Discussions](https://github.com/jjviscomi/bqemulator/discussions)
   if anything is unclear — see [SUPPORT.md](SUPPORT.md) for the right channel.

## Before you start (substantial changes)

- Read [AGENTS.md](AGENTS.md) — it captures our non-negotiable principles
  and day-to-day conventions.
- For architectural changes, open an [RFC](docs/rfcs/) before writing
  code.
- For substantial features, discuss scope in an issue first.

## Development setup

Requirements:

- Python 3.11, 3.12, or 3.13
- Docker with buildx (for building container images and e2e tests)
- Node.js 20+, Go 1.22+, and JDK 17+ if you plan to run the full e2e suite
- `make`

```bash
git clone https://github.com/jjviscomi/bqemulator
cd bqemulator
make dev-setup
```

`make dev-setup` installs the package in editable mode with all dev extras and
configures pre-commit hooks (including `commit-msg` for Conventional Commits).

## Branching

Trunk-based. `main` is always releasable.

- `feat/<short-slug>` for new features
- `fix/<short-slug>` for bug fixes
- `docs/<short-slug>` for documentation-only changes
- `refactor/<short-slug>` for refactors
- `test/<short-slug>` for test-only changes
- `chore/<short-slug>` for tooling, deps, CI
- `release/v<X.Y.Z>` for release branches

Keep branches short-lived (ideally merged within 3 days).

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/). The
`commit-msg` hook enforces this locally and CI enforces it on every PR.

Format:

```
<type>(<scope>)?: <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`.

Scope examples: `sql`, `catalog`, `rest`, `grpc`, `storage`, `udf`, `docs`,
`ci`, `deps`.

Examples:

- `feat(sql): add SAFE_DIVIDE translation rule`
- `fix(catalog): honor default_table_expiration_ms on insert`
- `docs(guides): add partitioning guide`

All commits must be signed (`git commit -S`) and DCO sign-off applied
(`git commit -s`).

## Pull requests

Every PR uses the template. Your PR description must include:

- **Summary** — what changed and why.
- **Motivation** — the problem being solved or the user value added.
- **Changes** — bullet list of notable changes.
- **Testing** — how the changes were tested; include e2e coverage
  confirmation.
- **Docs** — link to the guide / reference page / ADR updated or added.
- **Changelog** — confirm an entry under `Unreleased` in `CHANGELOG.md`.

### What must be true before you click "Ready for review"

- [ ] `make lint test-unit` passes locally
- [ ] New public functions have docstrings
- [ ] New features have unit tests AND e2e tests (for user-visible changes)
- [ ] Coverage on changed files is ≥90% (line and branch)
- [ ] Architectural decisions have an ADR
- [ ] Catalog schema changes have a migration
- [ ] `CHANGELOG.md` has an entry under `Unreleased`
- [ ] User docs updated when behavior changed
- [ ] CI is green

### Review policy

- **Steady state (multiple maintainers):** minimum **2 maintainer
  approvals** for behavioral changes; **1 approval + maintainer** is
  acceptable for trivial changes (typos, formatting, non-behavioral
  refactors).
- **Bootstrap phase (solo maintainer):** while [CODEOWNERS](CODEOWNERS)
  lists a single maintainer, the maintainer self-approves with all
  required status checks green. Behavioral changes still require a PR;
  the merge gate is "CI green + linear history + signed commits," not
  approval count. This carve-out sunsets the moment a second
  maintainer is onboarded.
- [CODEOWNERS](CODEOWNERS) auto-requests reviews from subsystem owners
  when reviewers exist.
- Reviewer SLA: first response within 24h business hours (steady
  state).

## Testing expectations

See [docs/architecture/testing-strategy.md](docs/architecture/testing-strategy.md)
for the full 7-tier pyramid plus mutation, differential, and fuzz
siblings.

Short version of what every PR must touch:

- **Unit (Tier 1)** — fast, hermetic, per-module.
- **Property (Tier 2)** — Hypothesis for combinatorial surfaces (SQL
  translation, type round-trips, scripting parser).
- **Integration (Tier 3)** — in-process emulator + the official Python
  client.
- **Conformance (Tier 5)** — recorded fixtures compared cell-by-cell
  against real BigQuery. Documented divergences live in
  [`tests/conformance/divergences.py`](tests/conformance/divergences.py)
  with ADR references; do not add a new divergence without one.
- **E2E (Tier 5 cont.)** — required for any user-visible feature. The
  same scenario must pass against all five conformance clients:
  Python, Node.js, Go, Java, and Google's `bq` CLI. See
  [ADR 0032](docs/adr/0032-bq-cli-conformance-client.md).

Tiers 4 (perf), 6 (chaos), 7 (perf-load) plus mutation / fuzz /
differential are gated nightly or on-demand — see
[docs/architecture/testing-strategy.md](docs/architecture/testing-strategy.md).

## Style and typing

- `ruff` (strict) + `ruff format` for lint and format.
- `mypy --strict` — no `Any`, no implicit `Optional`.
- Google-style docstrings.
- Keep functions small. Prefer composition over inheritance.

## Documentation

- Every user-facing feature has a guide in `docs/guides/`.
- Every non-trivial decision has an ADR in `docs/adr/`.
- Every example in `docs/examples/` is runnable and CI-verified.
- `mkdocs build --strict` must pass.

## Scope questions

If in doubt about whether something is in scope, check
[docs/reference/out-of-scope.md](docs/reference/out-of-scope.md) and open
an RFC. We follow the **no-deferral principle**: either we build the feature
in its phase or we exclude it cleanly with rationale.

## License and DCO

By contributing, you agree that your contributions are licensed under
Apache 2.0 (see [LICENSE](LICENSE)).

We use a [Developer Certificate of Origin](https://developercertificate.org/)
sign-off. Add `-s` to your `git commit` or write `Signed-off-by: Your Name
<you@example.com>` in your commit message.

## Questions

Open a [GitHub Discussion](https://github.com/jjviscomi/bqemulator/discussions)
for design questions, usage questions, and general help.
