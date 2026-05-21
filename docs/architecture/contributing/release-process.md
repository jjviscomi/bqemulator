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

2. **Branch off `main`.** Conventional name: `release/vX.Y.Z`.

3. **Dry-run the release locally.**

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

4. **Inspect the preview.** The terminal output is the contract — the
   operator confirms the version, date, commit message, and tag name
   match expectations before applying.

5. **Apply the release.**

    ```bash
    python scripts/release.py --apply --next minor
    ```

    This re-runs steps 1–3 of the dry-run for real, then:

    - Writes the new `__version__`.
    - Rewrites `CHANGELOG.md` (`Unreleased` → `[X.Y.Z] — YYYY-MM-DD`).
    - Stages every change with `git add -A`.
    - Creates the release commit (`release: bump to vX.Y.Z`).
    - Creates an annotated tag (`vX.Y.Z`). When
      `git config commit.gpgsign true` is set globally, the tag is
      signed automatically; the orchestrator does not force `-s`.

    At this point the new commit + tag are in your local clone only —
    nothing has hit the remote yet.

6. **Open a PR.** The release commit goes through CI like any other.
   The full gate chain must be green. CODEOWNERS approval rules apply.

7. **Merge to `main`.** Squash-merge per the repo convention.

8. **Push the tag.**

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

9. **Smoke-test the published artefacts.**

    ```bash
    docker pull ghcr.io/jjviscomi/bqemulator:X.Y.Z
    pip install "bqemulator==X.Y.Z"
    bqemulator version   # prints X.Y.Z
    ```

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
