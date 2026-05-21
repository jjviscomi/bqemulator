# ADR 0026: Mutation-tier design contract

- **Status**: Accepted

## Context

The [`docs/architecture/testing-strategy.md`](../architecture/testing-strategy.md)
document defines seven tiers of automated tests plus two
independent gates (fuzz and mutation) that sit alongside the
pyramid. Tiers 1–7 have shipped; the
Phase 11 roadmap doc lists
"mutation score tracked; regressions >2 points block release" as
a v1.0 ship criterion. That criterion has been aspirational since
Phase 0 — no baseline was ever recorded, so the gate could not
actually fail.

Two operational facts make mutation testing fundamentally different
from the seven-tier pyramid:

1. **A mutation score with no baseline is meaningless.** A run that
   ends "killed=420, survived=12, score=97.3%" is not actionable
   without a prior reference point. Like Tier 6 (performance),
   Tier mutation's unit of analysis is a *comparison*, not an
   *assertion*.
2. **First-run cost dwarfs steady-state cost.** Mutmut applies
   thousands of source-tree mutations and runs the unit suite once
   per mutant. The first run takes hours; subsequent runs reuse
   ``mutants/`` cache and only re-test mutants whose source
   neighbourhood changed. The CI cadence has to match that asymmetry.

This ADR captures the contract every mutation run must honour. The
two-point regression threshold was already set by the Phase 11 doc
and the v1-confidence-plan; this ADR locks in the *scope*,
*storage*, and *gate plumbing*.

## Decisions

### 1. The mutation gate is a v1.0 ship criterion, not a pyramid tier

Tiers 1–7 are listed in
[`docs/architecture/testing-strategy.md`](../architecture/testing-strategy.md).
Mutation testing is intentionally **not** numbered into the
pyramid — it sits alongside Tier 6 (performance) as an *independent
comparison gate* whose unit of analysis is a delta from a stored
baseline, not an intrinsic assertion. The fuzz harness (P3.c) will
take the same shape when it lands.

The gate fails the release when the mutation score drops more than
2 percentage points below the committed baseline. That bound is the
Phase 11 contract; this ADR codifies it in
[`scripts/check_mutation_baseline.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/check_mutation_baseline.py)
and wires it into ``make test-mutation``.

### 2. v1.0 ships a *pilot* scope; broader scope is v1.0.x

Mutmut applies ~5–10 mutants per non-comment LOC. The full
``src/bqemulator/`` source tree (after the structural exclusions in
decision 3) is ~27 000 lines spread over 133 modules — a first run
would burn 10+ hours of wall-clock and ~80% of the resulting
surviving mutants would either be in code exercised only through
integration / e2e tiers (slow per-mutant) or in framework-driven
modules whose mutants are functionally equivalent (FastAPI routes,
gRPC servicers, Click decorators). Scoring on uncoverable surface
inflates the baseline and produces a gate that flakes on coverage
churn alone.

The v1.0 pilot scope mutates **nine modules** — pure-domain,
deterministic, with strong direct unit-test coverage:

| Module | LOC | Tests |
|---|---|---|
| ``src/bqemulator/catalog/etag.py`` | 40 | ``tests/unit/catalog/test_etag.py`` |
| ``src/bqemulator/sql/cache.py`` | 136 | ``tests/unit/sql/test_cache.py`` |
| ``src/bqemulator/scripting/lexer.py`` | 299 | ``tests/unit/scripting/test_lexer.py`` |
| ``src/bqemulator/scripting/frames.py`` | 128 | ``tests/unit/scripting/test_frames.py`` |
| ``src/bqemulator/scripting/exceptions.py`` | 65 | ``tests/unit/scripting/test_exceptions.py`` |
| ``src/bqemulator/scripting/ast.py`` | 180 | (exercised through ``test_parser.py`` + ``test_interpreter.py``) |
| ``src/bqemulator/jobs/error_mapper.py`` | 420 | ``tests/unit/jobs/test_error_mapper.py`` |
| ``src/bqemulator/types/interval.py`` | 370 | ``tests/unit/types/test_interval.py`` |
| ``src/bqemulator/types/range_type.py`` | 157 | ``tests/unit/types/test_range_type.py`` |

These nine modules collectively own the deterministic,
framework-free *invariants* the project rests on (ETag stability,
LRU eviction, scripting lexer / frame semantics, INTERVAL & RANGE
arithmetic, DuckDB → BigQuery error mapping). A regression in any
of them silently breaks the wire-shape contracts the v1.0 ship
criteria depend on — exactly the surface where mutation testing
beats line coverage.

The v1.0.x roadmap entry for "mutation scope expansion" sweeps in
the next concentric ring — ``catalog/memory_repository.py``,
``sql/translator.py``, ``sql/rules/*``, ``versioning/``,
``row_access/policy.py``, ``udf/``, ``storage/arrow_bridge.py`` —
once the pilot's CI cadence has proven out.

### 3. Hard-excluded surfaces (``do_not_mutate``-equivalent)

Even when scope expands in v1.0.x, the following surfaces are
permanently out of the mutation tier:

| Path | Reason |
|---|---|
| ``src/bqemulator/grpc_api/proto/`` | Generated protobuf stubs; no semantic logic to mutate. |
| ``src/bqemulator/observability/`` | structlog / OTel / Prometheus wiring — mutants flip logger names or counter labels, not behaviour. |
| ``src/bqemulator/testing/`` | Test fixtures and helpers exercised through the e2e / CI tiers, not ``tests/unit/``. |
| ``src/bqemulator/api/routes/`` | FastAPI route handlers — exercised primarily through ``tests/integration``; per-mutant runtime is dominated by ASGI startup and most surviving mutants are wire-shape edge cases unit tests can't pin. |
| ``src/bqemulator/grpc_api/`` (excluding proto) | gRPC servicers — exercised through ``tests/integration``; same wall-clock problem as routes. |
| ``src/bqemulator/server.py`` / ``__main__.py`` / ``cli.py`` | Process bootstrap, uvicorn glue, Click decorators — many equivalent mutants on argv handling. |

The pilot scope is intentionally a strict subset of "everything not
on this list."

### 4. Score formula

Score = ``killed / (killed + survived)`` expressed as a percentage.
Both ``no_tests`` mutants (mutmut couldn't find a test that touches
the line at all) and ``skipped`` mutants are excluded from the
denominator. A ``no_tests`` mutant reflects a coverage-tier gap, not
a test-tier weakness; counting them would inflate or deflate the
score on coverage churn alone.

``timeout`` and ``suspicious`` mutants are also excluded — both are
infrastructure signals (the test runner hit its budget; the runner
produced an inconsistent exit code) rather than test-quality
signals.

The mapping is:

| mutmut status | Counted in numerator? | Counted in denominator? |
|---|---|---|
| ``killed`` | yes | yes |
| ``survived`` | no | yes |
| ``no_tests`` | no | no |
| ``skipped`` | no | no |
| ``timeout`` | no | no |
| ``suspicious`` | no | no |

### 5. Baseline is committed; updates are a deliberate operator action

``tests/mutation/baseline.json`` is committed to the repo. Every
field carries the recording date and the raw mutmut counts so a
reader can audit drift. The committed shape:

```json
{
  "score": 92.43,
  "killed": 423,
  "survived": 35,
  "no_tests": 9,
  "skipped": 0,
  "timeout": 0,
  "suspicious": 0,
  "total": 467,
  "run_at": "2024-05-19"
}
```

Re-baselining is a deliberate operator command:

```bash
make test-mutation-baseline   # overwrites tests/mutation/baseline.json
```

The forcing function mirrors performance baselines (ADR 0025) and
conformance fixtures: a baseline drift is a code change that lands
through review, not an automated diff in CI.

The committed JSON is the gate's reference. The
surviving-mutant *detail* (``mutants/mutmut-cicd-stats.json``)
is **not** committed — it leaks the implementation neighbourhood
without adding signal. Triage data lives on the CI artefact
attached to each nightly run.

### 6. Cadence: nightly, not on every commit

The full mutmut run takes 30+ minutes even on the pilot scope (and
hours on the full scope when v1.0.x expands it). Wiring it into
``make verify`` would 30× the CI runtime for a comparison whose
delta-from-baseline is mostly noise on a per-commit cadence — most
PRs touch zero of the nine pilot modules.

The
[`mutation.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/mutation.yml)
workflow runs nightly on ``main`` and uploads the surviving-mutant
detail as a CI artefact. PRs that touch a pilot module can opt in
via ``workflow_dispatch``; the gate's >2pp check fires on both
nightly and dispatched runs.

A regression detected by the nightly run blocks the next release
tag — not the in-flight PR that introduced it — because mutation
score is not a per-commit signal. The PR-level signal is the
existing 90% line + branch coverage gate; the mutation gate
catches *quality* drift that line coverage hides.

### 7. ``mutate_only_covered_lines`` is off (v1.0 pilot)

Mutmut's ``mutate_only_covered_lines = true`` option uses
[`coverage.py`](https://coverage.readthedocs.io/) to identify lines
the unit suite *actually executes*, then mutates only those lines.
On a larger scope it pays for itself by avoiding thousands of mutants
on dead code. On the v1.0 pilot scope (nine modules, ~1 800 LOC,
near-100% direct unit coverage) the saving is small.

It also costs us a Python-3.14 compatibility hazard. Coverage's
trace function interferes with DuckDB's C-extension submodule
registration (``_duckdb._sqltypes``) when ``import duckdb`` runs
under ``coverage.collect()``; the failure surfaces as
``ModuleNotFoundError: No module named '_duckdb._sqltypes'`` only in
the coverage-gather phase of ``mutmut run``. The setting is off for
the pilot to keep the toolchain stable on the project's reference
Python (3.14).

The v1.0.x scope-expansion entry will re-evaluate enabling it once
either (a) the upstream DuckDB / coverage.py interaction is fixed
or (b) the v1.0.x scope includes enough uncovered lines that the
filter pays for itself.

### 8. mutmut 3.x configuration (not 2.x)

The ``mutmut>=2.4`` floor in ``pyproject.toml`` resolves to
mutmut 3.5+ in modern Python environments. The 3.x config differs
from 2.x in three ways the project depends on:

| Aspect | 2.x | 3.x |
|---|---|---|
| ``paths_to_mutate`` | comma-separated string | **list of strings** |
| ``tests_dir`` | comma-separated string | **list of strings** |
| ``runner`` | shell command string | **removed** — pytest is invoked in-process |
| Working dir | ``.mutmut-cache`` | ``mutants/`` (a parallel copy of the source tree) |

The
[`[tool.mutmut]`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml)
section in ``pyproject.toml`` reflects 3.x shape. ``also_copy``
is set to the full ``src/bqemulator/`` tree so the package remains
importable when pytest runs from inside ``mutants/`` (mutmut 3.x
copies only ``paths_to_mutate`` files by default, which would
break ``import bqemulator.foo`` for any module outside the scope).

## Consequences

- **Positive.** The Phase 11 ship criterion "mutation score
  tracked; regressions >2 points block release" is now enforceable.
  Phase 11's contract was aspirational for nine months; this
  session *recorded* the baseline and wired the comparison.

- **Positive.** Score-formula choice (excluding ``no_tests`` and
  ``skipped`` from the denominator) means the gate measures
  *test-suite quality*, not *coverage*. A drop in line coverage
  doesn't move the mutation score; only a drop in the suite's
  ability to *distinguish* good code from mutated code does.

- **Positive.** A nightly CI cadence + per-PR coverage gate is
  cheaper than running both per-PR. Nightly is the right cadence
  for a multi-hour comparison whose value is delta-from-baseline.

- **Negative.** A pilot scope of nine modules covers a small
  fraction of ``src/bqemulator/``. The v1.0 ship criterion is
  *met* (the gate fires; the baseline is real), but the gate
  catches regressions only in the pilot surface. A regression in,
  e.g., ``sql/rules/spatial.py`` won't be caught by Tier mutation
  until v1.0.x sweeps the SQL-rule cluster into scope.

- **Negative.** Mutmut 3.x's ``mutants/`` working directory
  conflicts with the convention 2.x established (``.mutmut-cache``).
  The Makefile's ``clean`` target and ``.gitignore`` were updated
  in the same PR.

- **Negative.** First-run wall clock is non-trivial. A nightly job
  budget of 30+ minutes is reasonable; a per-commit budget of 30+
  minutes would block PR merge, which is why the gate is nightly.

- **Negative — local-recording limitation on macOS.** Mutmut 3.5.0
  calls ``multiprocessing.set_start_method('fork')`` at module
  level in ``mutmut/__main__.py`` and then uses ``os.fork()``
  directly for each mutant. macOS prohibits ``fork()`` after the
  parent process has loaded native extensions that touch the
  Objective-C runtime (DuckDB, pyarrow, structlog, mini-racer in
  this project); the child segfaults on first non-trivial work and
  the mutant is recorded as ``segfault``. The effect: a local
  ``make test-mutation`` on a developer's macOS box records every
  mutant as segfault and yields a 0 % score. The
  [`mutation.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/mutation.yml)
  workflow runs on Linux GitHub-Actions runners where ``fork()``
  is not subject to the same restriction, so the nightly gate (and
  the ``record-baseline=true`` ``workflow_dispatch`` mode) work
  correctly. Re-baselining is therefore a CI-side operation: the
  operator dispatches the workflow with ``record-baseline=true``,
  downloads the ``mutation-baseline`` artefact, and commits it.
  Until upstream mutmut accepts a fix to use ``spawn`` semantics
  for child processes (tracked in the v1.0.x mutation-scope
  expansion entry), this asymmetry stands.

## Implementation notes

- Pilot scope is encoded in
  [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml)
  ``[tool.mutmut]`` ``paths_to_mutate``.
- ``mutmut>=2.4`` floor is kept in the ``[dev]`` extra (the project
  uses 3.x in practice, but the floor is honest about minimum
  compatibility).
- ``make test-mutation`` runs ``mutmut run`` + ``mutmut export-cicd-stats``
 + the regression-check script and fails when the score drops
   >2pp.
- ``make test-mutation-baseline`` re-runs and overwrites the
  committed baseline; intended for operator use after the test
  suite has materially expanded.
- ``scripts/check_mutation_baseline.py`` reads
  ``mutants/mutmut-cicd-stats.json`` (mutmut's output) and compares
  to ``tests/mutation/baseline.json`` (committed).
- ``.github/workflows/mutation.yml`` schedules the nightly run and
  uploads the surviving-mutant artefact for triage.

## References

- Mutation testing subsection in the Phase 11 roadmap doc
- [Mutation testing in the testing-strategy doc](../architecture/testing-strategy.md)
- [ADR 0025](0025-perf-tier-design-contract.md) — Tier 6 perf gate
  is structurally analogous (compare-to-baseline, deliberate
  re-record, per-arch storage)
- v1-confidence-plan workstream P4.a
  — this ADR closes the workstream
