# ADR 0037: Adopt OpenSSF Scorecard — public security-posture score + badge

- **Status**: Accepted

## Context

The project ships security-relevant artefacts every release:
sigstore-attested PyPI wheels, cosign-keyless GHCR images, signed
commits + signed annotated tags, SHA-pinned third-party Actions, full
branch protection on `main`, CodeQL static analysis, Dependabot, and
a seven-tier test pyramid with ≥ 90% coverage. Each of those is
documented in CI workflow files or AGENTS.md, but there is **no
single, externally-verifiable scorecard** that says "this project
is doing those things." Downstream consumers — security teams
auditing the dependency, OSSF tooling
([`deps.dev`](https://deps.dev/), Open Source Insights,
[`allstar`](https://github.com/ossf/allstar)) — have to walk every
file and re-derive the posture themselves.

[OpenSSF Scorecard](https://scorecard.dev/) is the standard fix for
this: an automated checker that grades a public repository against
~18 security practices and publishes the score (0–10) to a
public database at
`https://api.securityscorecards.dev/projects/github.com/<owner>/<repo>`.
The score lights up a badge endpoint, surfaces in deps.dev, and is
treated by OSSF Best Practices as the canonical security-posture
metric for OSS.

This ADR adopts Scorecard with publication opt-in and adds the badge
to the README. The release orchestrator (`scripts/release.py` via
`scripts/bump_version.py`) already bumps shields.io badge
cache-bust suffixes; this ADR extends the bump to the Scorecard
badge's distinct `?v=X.Y.Z` cache-bust pattern.

## Decision

Add a new `.github/workflows/scorecard.yml` workflow that runs the
official `ossf/scorecard-action` against `main` on every push,
every published release, and weekly via cron. The workflow:

1. Computes the Scorecard score (SARIF output).
2. **Publishes the result** (`publish_results: true`) to the OSSF
   public database. The project owner has consented; the
   project is public anyway so the score is not novel info.
3. Uploads the SARIF as a 7-day-retention artefact for offline
   debugging.
4. Uploads the SARIF to GitHub's Security tab via
   `github/codeql-action/upload-sarif` so findings appear
   alongside CodeQL alerts.

The README badge stack gains a Scorecard badge linking to the
public viewer:

```markdown
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/jjviscomi/bqemulator/badge?v=X.Y.Z)](https://scorecard.dev/viewer/?uri=github.com/jjviscomi/bqemulator)
```

The `?v=X.Y.Z` query parameter is a GitHub-camo cache-bust suffix —
`api.securityscorecards.dev` ignores it but camo keys its image
cache on the full URL, so bumping `v=` on release forces readers'
browsers to refetch the freshly re-scored badge. The bump is
handled by `scripts/bump_version.py`'s extended regex
(`_README_SCORECARD_BADGE_RE`) alongside the existing shields.io
PyPI badges.

### Triggers

| Trigger | Why |
|---|---|
| `push: main` | Re-score on every change to the default branch — catches regressions immediately (e.g. a dropped permission scope, a downgraded action pin). |
| `release: published` | Re-score after each tag publishes — the badge then reflects the freshly attested wheel + signed image. |
| `schedule: 47 3 * * 1` | Weekly cron picks up dependency drift the per-push runs miss (a transitive dep that was clean Monday and CVE'd Wednesday). |
| `workflow_dispatch` | Manual re-run from the Actions UI for one-off rescore requests. |

### Permissions

Per the Scorecard action's [installation guide](https://github.com/ossf/scorecard-action#installation):

| Permission | Why |
|---|---|
| `security-events: write` | Required to push SARIF to GitHub Security tab. |
| `id-token: write` | Required for sigstore OIDC signature on the published score (proves the score came from THIS workflow, not a forged submission). |
| `contents: read` | Source checkout. |
| `actions: read` | Dependabot scan reads workflow runs. |

The top-level `permissions: read-all` follows Scorecard's own
hardening recommendation — the workflow's outer scope is read-only,
and the per-job permissions block grants the narrow writes the
action actually needs.

### Action pinning

| Action | Pin | Reason |
|---|---|---|
| `actions/checkout` | `@v4` | First-party `actions/*` — major-tag allowed per AGENTS.md OpenSSF-alignment rule. |
| `actions/upload-artifact` | `@v4` | Same. |
| `ossf/scorecard-action` | `@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3` | Third-party — full commit SHA + trailing `# vX.Y.Z` comment. |
| `github/codeql-action/upload-sarif` | `@7211b7c8077ea37d8641b6271f6a365a22a5fbfa # v4.36.0` | `github/*` is not `actions/*` — SHA-pinned to match the strict reading of AGENTS.md. (The pre-existing `codeql.yml` uses `@v4` for `github/codeql-action`; surfaced as pre-existing tech debt in PR description, not fixed here.) |

Dependabot already monitors `.github/workflows/*.yml`, so the two
SHA pins keep moving forward automatically.

### Expected initial score

The repo already implements most of what Scorecard checks for:

| Check | Expected | Why |
|---|---|---|
| Branch-Protection | 9–10 | Ruleset 16726422 enforces signed commits, linear history, no force-push, required status checks. |
| Token-Permissions | 9–10 | Every workflow uses scoped `permissions:` blocks. |
| Pinned-Dependencies | 9–10 | All third-party actions SHA-pinned (`grep -rEn 'uses:.*@v?[0-9]' .github/workflows/` returns no non-`actions/*` floating tags). |
| Signed-Releases | 9–10 | cosign keyless on GHCR, sigstore attestation on PyPI wheels (via Trusted Publishing). |
| Code-Review | 9–10 | All commits land via PR; CODEOWNERS enforces review. |
| CI-Tests | 10 | 27 CI checks per PR. |
| SAST | 10 | CodeQL on every PR. |
| CII-Best-Practices | 0 | Not yet enrolled — separate follow-up if we ever pursue the badge. |
| License | 10 | Apache-2.0 in repo root. |
| Maintained | 9–10 | Active commit cadence over the last 90 days. |
| Vulnerabilities | varies | OSV scan against the dependency tree; surfaces transitive CVEs even if Dependabot hasn't filed PRs yet. |
| Dependency-Update-Tool | 10 | Dependabot enabled across pip, github-actions, docker. |
| Webhooks | n/a | No deprecated webhooks in use. |
| Dangerous-Workflow | 10 | No `pull_request_target` with `actions/checkout` of forked SHA. |
| Fuzzing | low | Not formally enrolled in OSS-Fuzz; the `tests/fuzz/` corpus is local-only. Possible follow-up. |
| Packaging | 10 | Published packaged artefacts on PyPI and GHCR. |
| Binary-Artifacts | 10 | No committed binaries in repo. |
| Contributors | varies | Contributor diversity check — single-maintainer projects score lower regardless of practices. |

Realistic initial score: **8–9**. The two checks that drag the
score are CII-Best-Practices (not enrolled) and Contributors
(single-maintainer); the actionable ones (pinned deps, signed
releases, branch protection, SAST, code review) all score high.

### Initial-publish window

The Scorecard public database takes ~24–48 h to populate the first
score after the workflow lands. During that window the badge
endpoint returns 404 and renders as a broken image. This is
expected; no fix is required.

## Rationale

### Why publish (consent to `publish_results: true`)

The repo is public. Anyone who wants the score can run Scorecard
against it locally already — publication just makes the result
visible without that step. Publication also gates entry into
downstream OSSF tooling (deps.dev, Open Source Insights pull the
score from the public database; without publication those views
show "no data"). The project owner explicitly consented during
chip design.

### Why a separate workflow rather than extending `codeql.yml`

`codeql.yml` runs CodeQL static analysis on PRs. Scorecard is
neither static analysis nor PR-scoped — it's a holistic posture
check that runs against the default-branch state. Sharing a
workflow would either run Scorecard per-PR (wasteful) or run CodeQL
weekly (wasteful). Two separate workflows with their own triggers
is the right shape.

### Why bump the badge cache-bust on release

The Scorecard badge image is fetched through GitHub camo. Camo's
TTL is ~24 h keyed on the full URL; the score *itself* may have
re-computed within minutes of a release (push-on-main trigger), but
camo will still serve the stale badge image for up to a day. The
`?v=X.Y.Z` cache-bust suffix shifts the URL on every release so
camo refetches immediately. The Scorecard endpoint itself ignores
the unknown query param — verified against
`https://api.securityscorecards.dev/projects/github.com/jjviscomi/bqemulator/badge?v=anything`.

This is the **same** cache-bust trick already used on the
shields.io PyPI badges (PR #47, ADR n/a since that was tactical).
The only difference is the URL pattern: shields.io accepts
`?cacheSeconds=N&v=X.Y.Z` (two params); Scorecard accepts a bare
`?v=X.Y.Z`. The `_README_SCORECARD_BADGE_RE` regex anchors on
`api.securityscorecards.dev/.../badge?v=` so it never accidentally
matches an unrelated URL.

### Why the strict SHA pin on `github/codeql-action`

AGENTS.md's OpenSSF-alignment rule says first-party `actions/*` may
use major tags; everything else is SHA-pinned. `github/codeql-action`
is published under `github/`, not `actions/`, so the strict reading
of the rule is "SHA-pin it." The existing `codeql.yml` uses
`github/codeql-action@v4` (major tag) — that's pre-existing tech
debt this ADR notes but does not fix (separate PR if Scorecard's
Pinned-Dependencies check ever flags it).

## Consequences

### Positive

- **External, machine-readable security-posture signal.** A single
  URL replaces "trust me bro, we do all the right things."
- **Continuous regression detection.** A dropped permission scope,
  a downgraded action pin, a disabled branch protection rule —
  Scorecard catches each in its weekly + push-on-main cadence.
- **SARIF in GitHub Security tab.** Findings show alongside CodeQL
  alerts — single triage surface instead of two.
- **Downstream tooling lights up.** deps.dev / OSS Insights /
  allstar all pull from the published database.
- **Cheap.** The action is ~30 s per run; one workflow file.

### Negative

- **Initial-publish window 24–48 h.** The README badge is broken
  during that window. Documented in PR description; no action
  required, just patience.
- **Score drift on dependency CVEs.** The Vulnerabilities check
  pulls from OSV — a transient OSV index change can move the score
  even when nothing in the repo changed. This is true of any
  third-party scoring; the OSSF database surfaces history so a
  spike is debuggable.
- **One more workflow to maintain.** Pinned by Dependabot; the
  cost is one occasional PR-merge per upstream Scorecard release.

### Neutral

- The Pinned-Dependencies check may flag the pre-existing
  `github/codeql-action@v4` in `codeql.yml`. If it does, that's
  visible motivation to SHA-pin it in a follow-up. The current ADR
  does not bundle that fix.
- The Scorecard badge endpoint is fetched through camo on every
  README impression; the `?v=` cache-bust is what makes the
  release-time refresh reliable. Without the bump-on-release wiring
  (added by `_README_SCORECARD_BADGE_RE` in `scripts/bump_version.py`)
  the badge would lag by up to ~24 h after a release.

## Alternatives considered

1. **Don't adopt Scorecard.** Status quo: the security posture is
   real but invisible. Downstream consumers re-derive it manually
   if at all. Rejected — the entire point of OSSF tooling is to
   centralise this.
2. **Adopt Scorecard but don't publish (`publish_results: false`).**
   Keeps the score private. Loses every downstream benefit
   (deps.dev, OSS Insights, the public badge). The project owner
   considered this and chose to publish — the repo is public, so
   the score isn't novel info.
3. **Self-host the scoring (run the scorecard CLI in CI without
   the action).** Equivalent for the score itself, but skips the
   sigstore OIDC signature on the published result; OSSF database
   ingestion requires the signature for provenance. Rejected.
4. **Use CII Best Practices instead of Scorecard.** Different tool,
   different surface — CII Best Practices is a self-attestation
   questionnaire, not an automated check. Complementary, not a
   substitute. Possible future enrolment as a separate ADR.
5. **Bundle Scorecard into the existing `codeql.yml`.** Wrong
   trigger model (per-PR vs default-branch) and wrong cadence
   (CodeQL = every push; Scorecard = once per default-branch
   change + weekly). Rejected.
6. **Skip the cache-bust automation, accept ~24 h badge staleness.**
   Trivial savings (one regex, ~30 LoC). The badge is the public
   face of the score; a stale badge is the worst-case UX. Rejected.

## References

- [scorecard.dev](https://scorecard.dev/) — project home.
- [`ossf/scorecard`](https://github.com/ossf/scorecard) — scoring engine.
- [`ossf/scorecard-action`](https://github.com/ossf/scorecard-action) — GHA wrapper used here.
- [Scorecard installation guide](https://github.com/ossf/scorecard-action#installation) — workflow template + permissions doc.
- [ADR 0035](0035-code-quality-gates.md) and [ADR 0036](0036-complexity-ratchet-to-c.md) — template/shape this ADR follows.
- [PR #47](https://github.com/jjviscomi/bqemulator/pull/47) — the predecessor PR that introduced the README badge cache-bust automation this ADR extends.
- AGENTS.md "OpenSSF Scorecard alignment" — the project-wide rule that motivates the SHA-pinning convention.
