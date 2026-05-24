# ADR 0039: SLSA Build Provenance attestations on GitHub Release assets

- **Status**: Accepted

## Context

OpenSSF Scorecard (adopted by [ADR 0037](0037-openssf-scorecard.md))
scores the project's release-signing posture via the
``Signed-Releases`` check. The check inspects the **last 5 GitHub
Releases** and looks for signature files attached as release assets:
``.sig`` / ``.asc`` / ``.minisig`` / ``.sigstore`` / ``.intoto.jsonl``
suffixes. A release counts as "signed" only when one of these files
is attached **on the GitHub Release itself**, not on the downstream
distribution.

Pre-PR audit of the project's signing surface revealed three
asymmetries:

* ✅ **GHCR container image** — already attested via
  ``actions/attest-build-provenance@v1`` in
  ``.github/workflows/docker.yml``, plus a cosign keyless signature
  via OIDC. Scorecard recognises both.
* ✅ **PyPI wheel + sdist** — sigstore-attested via Trusted
  Publishing (``attestations: true`` on ``pypa/gh-action-pypi-publish``
  in ``.github/workflows/release.yml``). The attestations live on
  PyPI's side and are visible to ``pypi`` / ``sigstore`` tooling —
  but **not** to Scorecard, which only inspects the GitHub Release
  page.
* ❌ **GitHub Release assets** — every published tag (v1.0.0 / v1.0.1
  / v1.0.2) attaches the wheel + sdist via
  ``softprops/action-gh-release@v2`` but does **not** attach any
  signature file. From Scorecard's view the release is unsigned even
  though the wheel was sigstore-signed on PyPI five lines earlier.

The closure: add a GitHub-native attestation step in the
``github-release`` job so the same artifacts Scorecard inspects
carry a discoverable signature.

## Decision

Add ``actions/attest-build-provenance`` to the ``github-release``
job in ``.github/workflows/release.yml``, run it on every file under
``dist/`` (the wheel + sdist), and upload the resulting
``.intoto.jsonl`` SLSA Build Provenance bundle to the GitHub Release
alongside the artifacts. Pin by full commit SHA + trailing
``# vX.Y.Z`` comment per the OpenSSF-Scorecard-strict reading of
AGENTS.md (see "SHA pinning even for actions/*" below); same pin
applied to the existing ``docker.yml`` call site for consistency.

The pinned version is
``actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32 # v4.1.0``
— the latest stable as of 2026-05-23, producing SLSA Build
Provenance v1.0 schema. Dependabot already monitors
``.github/workflows/*.yml`` so the SHA moves forward automatically.

The workflow now:

1. Builds ``dist/`` via ``python -m build`` (``build`` job).
2. Publishes to PyPI with sigstore attestations (``publish-pypi``
   job).
3. **NEW**: Generates a SLSA Build Provenance attestation covering
   every file in ``dist/`` (``github-release`` job, ``attest`` step).
4. Attaches both the artifacts AND the attestation bundle to the
   GitHub Release.

After this lands, every release tag will surface an
``attestation.intoto.jsonl`` file on the GitHub Release page.
Downstream consumers can verify with:

```bash
gh attestation verify dist/bqemulator-X.Y.Z-py3-none-any.whl \
    --owner jjviscomi
```

and Scorecard's ``Signed-Releases`` check will count the release as
signed.

### Three implementation options considered

| Option | Shape | Decision |
|---|---|---|
| **A — GitHub Artifact Attestations** | ``actions/attest-build-provenance`` natively populates GitHub's attestations API + emits a ``.intoto.jsonl`` bundle we upload to the Release. SLSA v1.0 provenance schema. Sigstore keyless via OIDC. | ✅ **Chosen.** |
| **B — sigstore-python sign + upload** | Install ``sigstore`` Python CLI, run ``sigstore sign`` against each dist file, upload ``.sigstore`` bundles to the Release. Equivalent score impact; sigstore-only (no SLSA schema). | Rejected. More moving parts (extra dep) for the same Scorecard outcome. |
| **C — GPG-sign + ``.asc`` files** | Old-school PGP signatures. Requires a managed signing key. | Rejected. Key management overhead with no marginal score benefit. |

Option A wins on three axes:

1. **First-party GitHub action** (``actions/*``) — actively
   maintained by GitHub itself + Dependabot tracks it. Pinned by
   full commit SHA in this PR despite AGENTS.md's relaxed
   major-tag rule for ``actions/*``, because OpenSSF Scorecard's
   ``Pinned-Dependencies`` check rewards commit-SHA pinning
   regardless of action provenance (see SHA-pinning section
   below).
2. **SLSA v1.0 provenance** — strictly more information than a bare
   signature (includes builder identity, source repo, ref, workflow
   run URL). Future SLSA-aware tools (deps.dev, in-toto verifiers)
   light up automatically.
3. **Verifiable without external tooling** — ``gh attestation
   verify`` ships with the GitHub CLI; no separate sigstore install
   required by consumers.

## Why this PR also touches ``docker.yml``

``docker.yml`` already runs ``actions/attest-build-provenance``
against the container image digest, but at floating ``@v1`` (SLSA
Build Provenance v0.2 schema). Two reasons to roll the bump into
this same PR:

1. **Consistency**: both call sites should target the same
   version. Splitting them creates a window where the wheel +
   sdist attestation uses SLSA v1.0 while the container
   attestation still uses v0.2 — confusing for verifiers.
2. **Single SHA pin to maintain**: Dependabot bumps a pin in N
   files at once, so having both at the same SHA from day one
   keeps the cycle clean.

The bump moves ``docker.yml`` from floating ``@v1`` → SHA-pinned
``@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32 # v4.1.0``. SLSA v1.0
is a strictly richer schema than v0.2; downstream verifiers
recognise both, so this is forward-compatible.

## Expected Scorecard impact

The ``Signed-Releases`` check inspects the **last 5 releases**.
Recovery trajectory once this lands and v1.1.0 publishes:

| After release | Signed of last 5 | ``Signed-Releases`` score |
|---|---|---|
| v1.0.2 (today) | 0/3 | ~0/10 |
| v1.1.0 | 1/4 | ~2/10 |
| v1.1.1 | 2/5 | ~4/10 |
| v1.1.2 | 3/5 | ~6/10 |
| v1.1.3 | 4/5 | ~8/10 |
| v1.1.4 | 5/5 | **10/10** |

Tags are immutable on GitHub so we can't retroactively attest
v1.0.0–v1.0.2. The recovery is gradual but monotone — every new
tag improves the score until the rolling 5-release window
contains only signed entries.

Composite Scorecard score lift: ~+1.5 immediately after v1.1.0;
~+2.5 by v1.1.4.

## Rationale

### Why store the attestation as a Release asset (not just on the
attestations API)

GitHub's attestations API (``gh attestation verify``) is the
canonical verification surface — it reads the same data
``actions/attest-build-provenance`` writes. But Scorecard
specifically inspects **release assets** for known signature
suffixes. Without uploading the ``.intoto.jsonl`` bundle to the
Release, the API contains the attestation but Scorecard can't see
it. Belt-and-suspenders: bundle goes to BOTH the attestations API
AND the Release page.

### Why ``subject-path: 'dist/*'`` and not per-file

``actions/attest-build-provenance`` accepts a glob and produces a
SINGLE attestation bundle referencing all matched subjects. The
resulting ``.intoto.jsonl`` contains one statement per file (each
with its sha256 digest). Consumers verifying any specific file get
the same bundle; the action de-duplicates the upload step.

### SHA pinning even for ``actions/*``

AGENTS.md's OpenSSF-alignment rule allows first-party ``actions/*``
to use floating major tags (``@v4``) on the rationale that
Dependabot tracks them aggressively. That rule is a pragmatic
compromise, not the ceiling. **OpenSSF Scorecard's
``Pinned-Dependencies`` check gives full credit for commit-SHA
pinning regardless of action provenance**; major-tag pins earn
partial credit. For Scorecard score optimisation (which is the
proximate driver of this PR), SHA-pinning is strictly better.

Decision: SHA-pin ``actions/attest-build-provenance`` in both
``release.yml`` and ``docker.yml``. The pre-existing
``actions/checkout@v4`` / ``actions/setup-python@v5`` /
``actions/upload-artifact@v8`` etc. major-tag pins are
out-of-scope for this PR (separate sweep if we want to maximise
the ``Pinned-Dependencies`` score).

### Why v4.1.0 (the latest)

v4.1.0 (released 2026-02-26) is the current stable. Earlier v3.x
also produces SLSA v1.0 but lacks fixes for two edge cases the v4
line addressed (multi-subject batch upload + transparency-log
inclusion proof). No reason to pin to anything older than v4.1.0.

### Why the bump in ``softprops/action-gh-release``'s ``files:`` is
multi-line

YAML's ``files: dist/*`` single-line form does NOT expand to also
include ``${{ steps.attest.outputs.bundle-path }}`` — those are
separate paths. The multi-line ``files: | dist/* ${{ ... }}`` form
unions both. The action accepts shell-glob + explicit paths
together.

## Consequences

### Positive

* Every release tag from this PR onwards surfaces a SLSA Build
  Provenance attestation as a discoverable release asset.
* Scorecard's ``Signed-Releases`` check trajectory: 0 → 10 over 5
  releases.
* Downstream consumers gain a first-class verification path via
  ``gh attestation verify`` — no separate sigstore install
  required.
* SLSA v1.0 provenance unlocks downstream SLSA-aware verifiers
  (in-toto, slsa-verifier) without additional work.

### Negative

* ~10s extra runtime per release (sigstore OIDC handshake +
  bundle upload).
* One more failure surface — if the attestation step errors, the
  ``github-release`` job fails and the tag's release page won't
  publish until the issue is fixed. Mitigation: the failure mode
  is loud + the tag is immutable, so a fix-forward via re-run is
  the operator's path (same as any other workflow failure today).

### Neutral

* The PyPI Trusted Publishing sigstore attestations are
  preserved unchanged. PyPI consumers still get PyPI-native
  signatures via ``pip install`` verification flows. The
  GitHub-Release attestation is the GitHub-visible parallel — not a
  replacement.
* ``docker.yml`` updated to the same SHA-pinned v4.1.0 as the new
  ``release.yml`` step — both call sites are now consistent (same
  schema, same pinned commit, same Dependabot upgrade cadence).

## Alternatives considered

1. **Option B — ``sigstore`` Python CLI sign + upload.**
   Equivalent score impact, more moving parts, same outcome.
   Rejected.
2. **Option C — GPG ``.asc`` signatures.** Requires a managed
   signing key + key rotation. No marginal score benefit.
   Rejected.
3. **Skip the GitHub-Release attestation and accept the
   low score.** Fastest, but fails the user-mandated "improve
   Scorecard posture" requirement.
4. **Move PyPI sigstore attestations onto the GitHub Release.**
   PyPI's ``gh-action-pypi-publish`` doesn't expose its sigstore
   bundles as outputs the way ``actions/attest-build-provenance``
   does. Possible but more code; the action-based approach is
   cleaner.

## References

* [``actions/attest-build-provenance``](https://github.com/actions/attest-build-provenance) — the action used here.
* [SLSA Build Provenance v1.0 spec](https://slsa.dev/spec/v1.0/provenance) — the schema this attestation produces.
* [OpenSSF Scorecard ``Signed-Releases`` check](https://github.com/ossf/scorecard/blob/main/docs/checks.md#signed-releases) — what we're scoring against.
* [ADR 0037](0037-openssf-scorecard.md) — the Scorecard adoption that surfaced this gap.
* [`gh attestation verify`](https://cli.github.com/manual/gh_attestation_verify) — consumer-side verification path.
