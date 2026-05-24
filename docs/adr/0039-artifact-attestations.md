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

Add ``actions/attest-build-provenance@v3`` to the ``github-release``
job in ``.github/workflows/release.yml``, run it on every file under
``dist/`` (the wheel + sdist), and upload the resulting
``.intoto.jsonl`` SLSA Build Provenance bundle to the GitHub Release
alongside the artifacts.

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

1. **First-party GitHub action** (``actions/*``) — major-tag pinning
   allowed per AGENTS.md OpenSSF-alignment rule. Dependabot
   maintains it.
2. **SLSA v1.0 provenance** — strictly more information than a bare
   signature (includes builder identity, source repo, ref, workflow
   run URL). Future SLSA-aware tools (deps.dev, in-toto verifiers)
   light up automatically.
3. **Verifiable without external tooling** — ``gh attestation
   verify`` ships with the GitHub CLI; no separate sigstore install
   required by consumers.

## Why this PR doesn't touch ``docker.yml``

``docker.yml`` already runs ``actions/attest-build-provenance@v1``
against the container image digest (line 63), and Scorecard
already credits the container image as signed. The asymmetry was
purely on the PyPI wheel / GitHub Release side. Bumping
``docker.yml``'s pin from v1 → v3 is a parallel improvement
(SLSA v0.2 → v1.0 schema) but unrelated to closing the
``Signed-Releases`` gap; it's a follow-up if the maintainer wants
the newer schema, not a prerequisite.

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

### Why ``@v3`` (not ``@v1`` to match docker.yml)

``actions/attest-build-provenance@v3`` produces SLSA Build
Provenance v1.0 — the current standard schema. v1 produces SLSA
v0.2 which is technically deprecated though Scorecard still
recognises it. New code should target v1.0; the ``docker.yml``
``@v1`` is pre-existing tech debt for a separate bump PR.

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
* ``docker.yml`` continues at ``@v1``; the parallel-v3 bump is
  documented as out of scope.

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
