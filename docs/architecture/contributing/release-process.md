# Release process

Automated by [`scripts/release.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/release.py)
(orchestrator) + [`.github/workflows/release.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/release.yml)
+ [`.github/workflows/docker.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/docker.yml).

## Versioning

Semver: `MAJOR.MINOR.PATCH`. Pre-1.0 MINOR may include breaking changes
documented in the changelog. Post-1.0 breaking changes only in MAJOR,
preceded by ≥1 MINOR with deprecation warnings. Deprecated features
remain for ≥2 MINOR versions or 6 months.

## Release tooling

Three Python scripts back the release flow:

| Script | Responsibility |
|---|---|
| [`scripts/bump_version.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/bump_version.py) | Update `__version__` in `src/bqemulator/__init__.py`. Validates the new version is strictly greater than the current. |
| [`scripts/changelog.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/changelog.py) | Move the `## [Unreleased]` body into a new `## [X.Y.Z] — YYYY-MM-DD` section. Refuses to finalise an empty `Unreleased` section by default. |
| [`scripts/release.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/release.py) | Orchestrator. Runs `make verify`, calls the two scripts above, and creates the release commit + tag. Default mode is `--dry-run`; pass `--apply` to mutate state. |

All three scripts emit distinct exit codes per failure mode so
`release.yml` and operator scripting can pin the abort point (see the
`EXIT_*` constants at the top of each script).

## Quick reference

```bash
# 1. Preview the release (no files touched, no git state changed):
python scripts/release.py --dry-run --next minor
# or
make release-dry-run NEXT=minor

# 2. Apply the release (runs make verify, mutates files, commits, tags):
python scripts/release.py --apply --next minor
# or
make release NEXT=minor

# 3. Inspect + push:
git show v0.2.0
git push origin main v0.2.0

# 4. .github/workflows/release.yml fires on the tag push.
```

The orchestrator's hard preconditions (every one of which aborts with
a dedicated exit code):

1. The current directory is a git repository (`.git` present).
2. Working tree is clean (`git status --porcelain` is empty).
3. `make verify` exits 0 — the full release gate chain (lint + unit +
   property + integration + docker + e2e + docs).
4. The computed target version is strictly greater than the current.
5. `CHANGELOG.md`'s `Unreleased` section has at least one entry
   (override with `--allow-empty-changelog` for zero-impact patches).

## Step-by-step (with the orchestrator)

1. **Prepare the changelog.** Every PR that lands during the release
   cycle must add an entry under `## [Unreleased]`
   (`Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` /
   `Security`).

2. **Pre-release doc sweep.** The release orchestrator only mutates
   `src/bqemulator/__version__` (via `bump_version.py`) and
   `CHANGELOG.md` (via `changelog.py`). Every other version- or
   maturity-bearing string is **manual** and must be checked once per
   release. The audit:

   | File | What to update at a MAJOR / first-stable cut |
   |---|---|
   | [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml) | `Development Status` classifier (e.g. `3 - Alpha` → `5 - Production/Stable` at v1.0.0). Python version classifiers must match the CI test matrix in [`ci.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/ci.yml) — `pip install` users see this list on the PyPI page and on the `Python` shield in the README. |
   | [`README.md`](https://github.com/jjviscomi/bqemulator/blob/main/README.md) — "Project status" section | Drop "pre-1.0" / "currently 0.x.y" language; promote any `⚪` maturity rows for things now shipped (PyPI publish, GHCR publish, etc.). **Land this on the release branch itself (first commit, before `make release`)** — see the "README 'Project status' flip" subsection below. The rest of this sweep can land in a separate pre-release housekeeping PR. |
   | [`README.md`](https://github.com/jjviscomi/bqemulator/blob/main/README.md) — "Conformance corpus depth" header | If the snapshot date is older than ~30 days, regenerate with `make coverage-matrix` and update the prose. |
   | [`docs/getting-started.md`](https://github.com/jjviscomi/bqemulator/blob/main/docs/getting-started.md) + [`docs/reference/cli.md`](https://github.com/jjviscomi/bqemulator/blob/main/docs/reference/cli.md) | Example outputs that hard-code a version string (`{"status":"ok","version":"0.1.0"}`, `bqemulator 0.1.0`). |
   | All four auto-generated reference docs ([`conformance-coverage-matrix`](https://github.com/jjviscomi/bqemulator/blob/main/docs/reference/conformance-coverage-matrix.md), [`compatibility-matrix`](https://github.com/jjviscomi/bqemulator/blob/main/docs/reference/compatibility-matrix.md), [`sql-function-mapping`](https://github.com/jjviscomi/bqemulator/blob/main/docs/reference/sql-function-mapping.md), [`api-coverage`](https://github.com/jjviscomi/bqemulator/blob/main/docs/reference/api-coverage.md)) | Run `make matrix coverage-matrix` and commit any diff. The umbrella `make matrix` covers compat-matrix + function-mapping + api-coverage in one call; `make coverage-matrix` is separate because it walks the conformance corpus. The Docs-drift CI gate runs the matching `--check` modes on every PR. |
   | [`docs/reference/api-configuration-coverage-matrix.md`](https://github.com/jjviscomi/bqemulator/blob/main/docs/reference/api-configuration-coverage-matrix.md) | Manually-maintained audit doc (labelled "Audit dated" at the top — not auto-generated). Skim for new configuration knobs the release added; refresh the audit date if any new entry lands. |
   | `.dev/STATUS.md`, `.dev/v1-confidence-plan.md` | Internal status trackers — update before any external version claim references them. |

   Land this sweep as a pre-release housekeeping PR **before** running
   the orchestrator. The orchestrator's `make verify` step won't catch
   maturity drift; CI doesn't know your `Development Status` is stale.
   Treat the doc sweep as part of the release contract, not as an
   afterthought.

3. **Branch off `main`.** Conventional name: `release/vX.Y.Z`.

4. **Dry-run the release locally.**

    ```bash
    python scripts/release.py --dry-run --next minor
    ```

    This:

    - Verifies the working tree is clean.
    - Runs `make verify` (full gate chain). Pass `--skip-verify` only
      when debugging the release tooling itself.
    - Previews the proposed `__init__.py` bump.
    - Previews the proposed CHANGELOG finalisation.
    - Prints what the commit message + tag would be.
    - Returns 0 with the working tree completely untouched.

5. **Inspect the preview.** The terminal output is the contract — the
   operator confirms the version, date, commit message, and tag name
   match expectations before applying.

6. **Apply the release.**

    ```bash
    python scripts/release.py --apply --next minor
    ```

    This re-runs steps 4–5 of the dry-run for real, then:

    - Writes the new `__version__`.
    - Rewrites `CHANGELOG.md` (`Unreleased` → `[X.Y.Z] — YYYY-MM-DD`).
    - Stages every change with `git add -A`.
    - Creates the release commit (`release: bump to vX.Y.Z`).
    - Creates an annotated tag (`vX.Y.Z`). When
      `git config commit.gpgsign true` is set globally, the tag is
      signed automatically; the orchestrator does not force `-s`.

    At this point the new commit + tag are in your local clone only —
    nothing has hit the remote yet.

7. **Open a PR.** The release commit goes through CI like any other.
   The full gate chain must be green. CODEOWNERS approval rules apply.

8. **Merge to `main`.** Squash-merge per the repo convention.

9. **Push the tag.**

    ```bash
    git push origin main vX.Y.Z
    ```

    Pushing the tag fires
    [`.github/workflows/release.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/release.yml),
    which:

    - Builds the wheel + sdist with `python -m build`.
    - Publishes to PyPI via Trusted Publishing (sigstore attestation).
    - Creates the GitHub Release with auto-generated notes.

    In parallel, the tag also fires
    [`.github/workflows/docker.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/docker.yml),
    which publishes the multi-arch image to GHCR with cosign keyless
    signatures.

10. **Smoke-test the published artefacts.**

    ```bash
    docker pull ghcr.io/jjviscomi/bqemulator:X.Y.Z
    pip install "bqemulator==X.Y.Z"
    bqemulator version   # prints X.Y.Z
    ```

### README "Project status" flip (folded into the release PR)

Earlier iterations of this process kept the README's
*aspirational-vs-factual* wording in a **separate post-release
PR** that landed after step 10's smoke-tests. That meant every
release shipped a chicken-and-egg gap: the release commit
referenced a `vX.Y.Z-rc` README, and a follow-up PR had to land
with the same release notes to flip ⚪ → ✅. v1.0.0 collapsed the
two into a single PR; the convention since is to do the README
flip **before** running the orchestrator, so the bump commit and
the wording flip land in the same merge:

| File | What to flip (in the release branch, before `make release`) |
|---|---|
| [`README.md`](https://github.com/jjviscomi/bqemulator/blob/main/README.md) — "Project status" header | `vX.Y.Z-rc` / "staged on `main`" prose → factual "at **vX.Y.Z** — the initial production-stable release" wording. |
| [`README.md`](https://github.com/jjviscomi/bqemulator/blob/main/README.md) — "Maturity signals" rows | ⚪ "PyPI publish — wired and waiting on the tag push" → ✅ with the actual `pip install` / `docker pull` command. |

The trade-off is explicit: the README now claims the artefacts
exist a few minutes **before** the publish workflows finish.
That window closes within roughly 5–10 min of pushing the tag
(release.yml + docker.yml end-to-end). The convention is to
verify the artefacts (`pip install`, `docker pull`, cosign
verification) right after the tag push — if a workflow fails,
the next commit on `main` is the README revert, not a separate
"flip" PR.

## CLI reference

### `scripts/bump_version.py`

```bash
python scripts/bump_version.py 1.0.0            # explicit
python scripts/bump_version.py --major          # 0.1.0 -> 1.0.0
python scripts/bump_version.py --minor          # 0.1.0 -> 0.2.0
python scripts/bump_version.py --patch          # 0.1.0 -> 0.1.1
python scripts/bump_version.py --next minor     # alias for --minor
python scripts/bump_version.py --print          # report current; no mutation
python scripts/bump_version.py --next minor --check
                                                # validate without writing
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 2 | Usage error (malformed version, missing argument) |
| 3 | Proposed version not strictly greater than current |

### `scripts/changelog.py`

```bash
python scripts/changelog.py 1.0.0                 # finalise
python scripts/changelog.py 1.0.0 --date YYYY-MM-DD
python scripts/changelog.py 1.0.0 --check         # validate only
python scripts/changelog.py 1.0.0 --allow-empty   # empty Unreleased OK
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 2 | Usage error (malformed version or date, missing file) |
| 3 | No `## [Unreleased]` section in the changelog |
| 4 | `## [Unreleased]` has no entries (use `--allow-empty` to override) |
| 5 | The `## [X.Y.Z]` section already exists |

### `scripts/release.py`

```bash
python scripts/release.py --dry-run --next minor     # preview (default mode)
python scripts/release.py --apply --next patch       # full pipeline
python scripts/release.py --apply --version 1.0.0    # explicit version
python scripts/release.py --apply --next minor --skip-verify
                                                     # skip ``make verify``
python scripts/release.py --apply --next minor --allow-empty-changelog
                                                     # ship a no-changelog patch
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 2 | Argparse usage error |
| 10 | Not a git repository (or git missing on PATH) |
| 11 | Working tree is not clean |
| 12 | `make verify` failed |
| 13 | `bump_version` failed (version validation / file write) |
| 14 | `changelog` finalisation failed |
| 15 | `git commit` failed |
| 16 | `git tag` failed |

## Artifact signing

- **Docker images** signed with keyless cosign via GitHub OIDC. Verify:

    ```bash
    cosign verify ghcr.io/jjviscomi/bqemulator:X.Y.Z \
        --certificate-identity-regexp "github.com/jjviscomi/bqemulator" \
        --certificate-oidc-issuer https://token.actions.githubusercontent.com
    ```

- **PyPI wheels** carry sigstore attestations via Trusted Publishing.

## Abandoning a release locally

`scripts/release.py --apply` only mutates your local clone. If the
inspection after step 5 reveals a problem, you can back out cleanly:

```bash
git tag -d vX.Y.Z          # delete the local tag (it's not on the remote yet)
git reset --hard HEAD~1    # discard the release commit
```

Tags are immutable on GitHub. **Never** push a tag you intend to
re-build — push the corrected tag under a new version number.
