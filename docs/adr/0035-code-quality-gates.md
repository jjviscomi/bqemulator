# ADR 0035: Non-blocking code-quality gates — complexity, duplication, dead code

- **Status**: Accepted

## Context

The current pre-commit gate chain (`make lint` →
ruff + mypy --strict + bandit + pip-audit + interrogate + typos, plus
`test-unit` / `test-coverage` / `test-patch-coverage`) is dense but
leaves three holes:

| Concern | Current coverage | Gap |
|---|---|---|
| **Cyclomatic complexity** | ruff has the [mccabe `C901`](https://docs.astral.sh/ruff/rules/complex-structure/) rule plus [`PLR0911`](https://docs.astral.sh/ruff/rules/too-many-return-statements/), [`PLR0912`](https://docs.astral.sh/ruff/rules/too-many-branches/), [`PLR0913`](https://docs.astral.sh/ruff/rules/too-many-arguments/), [`PLR0915`](https://docs.astral.sh/ruff/rules/too-many-statements/). All four of the first set are deliberately **ignored** in `pyproject.toml` — the rationale is that type-dispatch and route-handler signatures are naturally branchy and adding per-function `noqa` is noise. Only `PLR0915` (50-statement ceiling) actively enforces anything. | No per-function rank ceiling; a function can climb to complexity 30+ without surfacing. |
| **Code duplication / DRY violations** | None. CodeQL doesn't catch it, ruff doesn't detect cross-file clones, Codecov doesn't model it. | A whole category — "this should've been a helper" — is invisible. |
| **Dead code (unused names)** | `vulture>=2.11` is in the dev-deps and `[tool.vulture]` config exists, but the tool is **not** invoked anywhere — not in `make lint`, not in any CI workflow. Dormant. | Same surface, but worse: the dependency is paid for without any return. |

The user concern that prompted this ADR was specifically "tools we have do
not do a good job at catching cyclomatic complexity, bad patterns, or
DRY violations across the codebase." Verified against the actual
`pyproject.toml` ignore list, which confirms the gap.

SonarCloud was considered explicitly and declined — the existing stack
(ruff + bandit + CodeQL + Codecov + CodeRabbit + the seven-tier test
pyramid) already covers most of what SonarCloud catches; the
marginal value over what the three targeted tools below provide is
mostly dashboard convenience, not bug-catching.

## Decision

Add three external gates, wired as **non-blocking** quality checks
behind a single `make quality` umbrella target and a dedicated
`.github/workflows/code-quality.yml` workflow. None of the three
become part of `make verify` in this ADR — that gate stays the strict
pre-merge contract. Promote-to-required is a separate follow-up PR
once a baseline cycle on `main` confirms the thresholds.

### 1. Cyclomatic complexity → `radon` + `xenon`

`xenon` wraps `radon`'s per-function complexity scoring with hard
threshold flags suitable for CI. It assigns each block a rank A–F
where the bands are:

| Rank | Cyclomatic complexity |
|---|---|
| A | 1–5 |
| B | 6–10 |
| C | 11–20 |
| D | 21–30 |
| E | 31–40 |
| F | ≥41 |

Baseline measured against the v1.0.2 codebase (1,531 blocks):

| Metric | Value |
|---|---|
| Project-wide average complexity | A (3.44) |
| Worst-ranked module average | C (`storage/arrow_bridge.py`, `versioning/time_travel.py`, `sql/rewriter/unnest_offset.py`, `sql/rewriter/timestamp_iso_helpers.py`) |
| Worst-ranked single function | E (39) — `storage/arrow_bridge._format_bq_value` |
| Blocks at rank ≥ C | 65 / 1,531 (4.2%) |
| Blocks at rank ≥ D | 10 / 1,531 (0.7%) |
| Blocks at rank ≥ E | 3 / 1,531 (0.2%) |

Thresholds chosen against that baseline so the gate passes today but
catches regression:

```make
xenon --max-absolute E --max-modules C --max-average A src/bqemulator
```

- `--max-absolute E` — no function above rank E (a new F-rank function
  fails).
- `--max-modules C` — no module averaging above rank C (a new high-
  complexity module fails even if no single function trips the absolute
  ceiling).
- `--max-average A` — project-wide average must stay rank A. Today's
  number is 3.44 — comfortable headroom before the A/B boundary at 5.

These are the **first-PR baseline**. The follow-up promote-to-required
PR will tune them downward once we see the noise floor on `main`.

`xenon` provides what the ignored ruff rules (`C901` / `PLR0911` /
`PLR0912`) can't: a per-function ceiling without requiring
per-occurrence `noqa`. The existing ruff ignores stay — they exist
because the type-dispatch pattern is structurally branchy, and that
pattern is exactly what produces D-rank functions today. The xenon
ceiling caps the worst case while leaving the pattern itself
unflagged.

### 2. Code duplication → `jscpd`

`jscpd` is the only Python-capable cross-file clone detector with
mature CI ergonomics. The Python-native alternative
(`pylint --disable=all --enable=duplicate-code`) is slower, has
weaker reporting, and runs against an already-loaded ruff/mypy
toolchain that does not include pylint.

`jscpd` is JS-based — wired via `npx -y jscpd@4` so the gate adds
zero permanent JS deps to the Python project. CI installs node 20
alongside Python via `actions/setup-node@v6`.

Baseline (v1.0.2, default thresholds 10 lines / 70 tokens):

| Metric | Value |
|---|---|
| Total clones | 7 (when grouped pair-wise) — `consoleFull` reporter expands to 19 fragment-level pairs |
| Duplicated lines | 112 |
| Project duplication ratio | 0.36% |

All seven baseline clones are 11–22 lines, structurally
template-shaped (paired streaming strategies, route-handler CRUD
shapes, paired SQL-rule helpers). Refactoring would introduce
premature abstraction — the structural-similarity-but-semantic-
distinctness is intentional. The threshold is set to **1.0%** —
today passes (0.36%), but any meaningful regression fails.

Config lives at `.jscpd.json`:

```json
{
  "pattern": "src/bqemulator/**/*.py",
  "ignore": ["**/tests/**", "**/conformance/**", ...],
  "min-lines": 10,
  "min-tokens": 70,
  "threshold": 1.0
}
```

### 3. Dead code → `vulture`

`vulture` is already a dev-dep with `[tool.vulture]` config in
`pyproject.toml` — but it was never wired into any target. This ADR
flips that on.

Baseline: **1 finding** at `min_confidence = 80` —
`jobs/executor.py:251: unused variable 'use_cache'`. The variable is
a reserved kwarg for future query-cache integration, documented
inline with `# noqa: ARG001`. vulture's "unused variable" check
fires independently of ruff's unused-argument suppression, so the
existing config doesn't catch it.

A new `.vulture_whitelist.py` lists the name with an inline
justification linked back to the source comment. Each future
whitelist entry lands via PR review only — that's the contract that
keeps the gate trustworthy.

The whitelist file is added to `[tool.vulture] paths`:

```toml
[tool.vulture]
paths = ["src/bqemulator", ".vulture_whitelist.py"]
min_confidence = 80
ignore_decorators = ["@app.*", "@router.*", "@pytest.fixture"]
ignore_names = ["*Command", "*Request", "*Response"]
```

### Wiring

| Surface | Where |
|---|---|
| `make quality-complexity` | `xenon ...` |
| `make quality-duplication` | `npx -y jscpd@4 --config .jscpd.json` |
| `make quality-dead-code` | `vulture` (reads `[tool.vulture]`) |
| `make quality` | Umbrella over all three |
| `.github/workflows/code-quality.yml` | Per-PR + push-to-main + nightly + manual; each step has `continue-on-error: true`; aggregated outcomes in `$GITHUB_STEP_SUMMARY` |
| `make verify` | **Unchanged**. The three gates are non-blocking — they don't gate releases yet. |

### Why non-blocking first

Per the v1.0 charter the project follows a **no-deferral principle**
on feature scope, but introducing new gates is operationally
different: a gate that fires on every PR without justification
trains developers to ignore it. The non-blocking phase exists to
let the thresholds settle against `main` over several normal
merges and to surface noise (false positives, structural-pattern
hits) before a regression starts blocking merges.

The follow-up promote-to-required PR will:

1. Tighten thresholds based on observed noise (e.g. drop
   `--max-absolute E` to `--max-absolute D` if no D-rank function
   merged in the meantime).
2. Add the relevant `make quality-*` step to `make verify`.
3. Add the check name to the branch protection ruleset's required
   list (`gh api repos/jjviscomi/bqemulator/rulesets/...`).
4. Re-confirm against the cycle's main HEAD.

## Rationale

### Why three separate tools, not one

The three concerns are independent. SonarQube / SonarCloud bundle
all three into one dashboard, but the marginal value over three
focused tools is low and the operational cost (SaaS billing,
token management, another bot reviewer) is non-trivial. The user
explicitly declined SonarCloud during the design discussion.

### Why xenon over relaxing the ignored ruff rules

The ignored rules (`C901` / `PLR0911` / `PLR0912` / `PLR0913`) are
**per-function** checks. Their failure mode is "this function
crosses threshold X" with no escape hatch except `# noqa: <rule>` on
the offending function. The current ignore list captures the
project-wide judgement that type-dispatch functions earn the
branch budget; surfacing them as warnings on every PR would
require landing dozens of `noqa` annotations across the rule
registry alone.

xenon's `--max-absolute E` instead caps the **worst case** in the
whole codebase — a D-rank type-dispatch function still passes; only
a brand-new function at F-rank (≥41 complexity) fails. Different
contract, more usable.

### Why jscpd over pylint --enable=duplicate-code

Performance: pylint loads the whole project's AST and runs ~100
other checks before reaching duplicate-code. jscpd is a focused
clone detector that finishes the same scan in <1s on this
codebase. Output quality: jscpd's `consoleFull` reporter prints
the actual cloned line ranges side-by-side; pylint's
duplicate-code reporter is more terse and less actionable.

### Why `npx -y jscpd@4` rather than installing it

The Python project has no other JS deps. Pinning jscpd through a
package-lock here would add a permanent maintenance surface (renovate
PRs, audit warnings on lockfile vulnerabilities) for a single
non-blocking gate. `npx -y` pulls the published package on demand;
CI's `actions/setup-node@v6` cache makes subsequent runs sub-second.
The local dev experience matches: any developer with node already
has npx. The `@4` major-version pin keeps the gate reproducible
without a lockfile: jscpd 4.x is stable; a future 5.x release that
breaks our config schema or threshold semantics requires a deliberate
opt-in bump.

### Why preserve the existing vulture config rather than reset it

The existing `ignore_decorators` / `ignore_names` patterns
(`@app.*`, `@router.*`, `@pytest.fixture`, `*Command` / `*Request` /
`*Response`) reflect actual project conventions — the decorators
mark FastAPI endpoint registrations (referenced reflectively), the
suffix patterns mark command/transport DTO classes that pydantic
serializes. Resetting them would generate false positives that the
whitelist would then have to absorb. Keep the suppressions, add
the missing whitelist for the one new finding.

## Consequences

### Positive

- Three real categories of code-quality drift now have an observable
  signal — promoted-to-required in a follow-up they'll have a
  preventive signal too.
- The user concern that prompted this work has a documented answer:
  `make quality` reports complexity / duplication / dead code; CI
  surfaces the same on every PR.
- vulture stops being a dev-dep that costs install bandwidth but
  produces nothing.
- The three tools' configs are independent — promoting any one
  alone to required doesn't depend on the others.

### Negative

- Three more tools to keep current. radon / xenon / vulture all
  follow the standard pip update cadence; jscpd is fetched fresh
  by npx every CI run so it tracks upstream automatically (and
  can therefore break on a major-version jscpd release — pin via
  `jscpd@4` if that becomes a problem).
- node + npm installed in the code-quality CI job — adds ~10-15s
  to that job's runtime. Acceptable: the workflow is non-blocking
  and not on the release path.
- The three baselines (xenon thresholds, jscpd 1.0%, vulture
  whitelist) capture a snapshot of v1.0.2. Each will need a
  revisit pass after some commits land on `main` — the promote-
  to-required PR is when that revisit happens.

### Neutral

- The Vulture whitelist file (`.vulture_whitelist.py`) is real
  Python source — it imports nothing and references names as
  bare statements. Ruff's S101/B018 fires on it without
  in-file suppressions; the file already carries the right
  `# noqa: F821, B018` annotations.

## Alternatives considered

1. **SonarQube / SonarCloud.** Dashboards + bundled checks, but
   overlaps with existing stack. Declined by the project owner
   during the design discussion.
2. **Add C901 / PLR0911 / PLR0912 / PLR0913 back to ruff with
   per-function `noqa` waivers.** Higher friction per regression
   (every type-dispatch function needs an inline annotation);
   doesn't give a project-wide ceiling. Rejected.
3. **Promote the gates to required in the same PR as adding them.**
   Locks in baselines that haven't seen a cycle on `main` yet —
   any noise we miss now would block PRs immediately. The
   prompt-design intent (the user task that drove this ADR)
   explicitly called for non-blocking-first.
4. **pylint full run for everything.** Slow, redundant with ruff
   for the rules ruff already covers, and the bits ruff doesn't
   (`duplicate-code`) are weaker than `jscpd`. Rejected.
5. **SonarCloud (free for OSS) + community quality-gate badge.**
   Same overlap argument as #1; the marginal value is dashboard
   convenience, not bug-catching. Re-openable via RFC.

## References

- [Issue #17 closure / v1.0.2 (PR #42)](https://github.com/jjviscomi/bqemulator/pull/42) — the release this ADR sequences after.
- [ADR 0033](0033-storage-read-arrow-ipc-bare-message-contract.md) and
  [ADR 0034](0034-scio-beam-emulator-routing.md) — the template/shape this ADR follows.
- [`radon` docs](https://radon.readthedocs.io/en/latest/) — rank tier definitions.
- [`xenon` docs](https://xenon.readthedocs.io/en/latest/) — CLI thresholds.
- [`jscpd` docs](https://github.com/kucherenko/jscpd) — config schema.
- [`vulture` docs](https://github.com/jendrikseipp/vulture) — whitelist semantics.
- AGENTS.md "Pre-commit gate (mandatory)" — the contract this ADR
  carefully **doesn't** extend yet.
