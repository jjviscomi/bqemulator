# Governance

This document describes how the bqemulator project is governed. It is
deliberately lightweight while the project is small; we will expand it
as the contributor base grows.

## Bootstrap phase

While [CODEOWNERS](CODEOWNERS) lists a single maintainer the project
is in **bootstrap phase**: the roles, decision-making, and review-quorum
rules below describe the *steady state*, not the current one.

In bootstrap phase:

- The sole maintainer fills the Contributor / Reviewer / Maintainer /
  TSC roles for any purpose that requires a quorum decision.
- Merges still go through PR + CI green + signed commits — the merge
  gate is *automation*, not *approval count*.
- New maintainers are added by invitation from the sole maintainer.
  Once a second maintainer joins, the steady-state rules below
  activate (TSC seats are filled by mutual consent until three
  maintainers exist).

The bootstrap clause sunsets automatically when the third maintainer is
onboarded; at that point the TSC convenes and the rules below apply
verbatim.

## Roles

### Contributor

Anyone who submits an issue, pull request, review, or discussion. All
contributors are bound by the [Code of Conduct](CODE_OF_CONDUCT.md).

### Reviewer

A contributor who has been granted the ability to review pull requests.
Reviewers typically have several merged contributions and have demonstrated
understanding of the codebase and conventions. Reviewers are listed in
[CODEOWNERS](CODEOWNERS) for the areas they cover.

### Maintainer

A contributor with write access to the repository. Maintainers are
responsible for:

- Triaging issues and pull requests.
- Ensuring CI health and release hygiene.
- Enforcing the Code of Conduct.
- Approving substantial changes.

New maintainers are added by consensus of the existing maintainers after a
track record of sustained, high-quality contribution.

### Technical Steering Committee (TSC)

A committee of three (3) maintainers that serves as tiebreaker for
contested technical decisions and as the final authority on RFCs that do
not reach consensus. TSC seats rotate annually.

Current TSC members (once three maintainers exist — see the
[Bootstrap phase](#bootstrap-phase)) will be listed in
[CODEOWNERS](CODEOWNERS) under a dedicated `# TSC` section so they are
auto-requested on RFC reviews.

## Decision making

### Day-to-day

Pull requests and routine issues are decided by the relevant reviewers and
maintainers via the usual PR review process. Conflicts escalate to a
maintainer; unresolved conflicts escalate to the TSC.

### RFCs

Changes to the public API, SQL semantics, persistence format, or governance
require an RFC (see [docs/rfcs/README.md](docs/rfcs/README.md)). RFCs move
through `Draft` → `Review` (≥2 weeks) → `Accepted` / `Rejected` / `Deferred`.

Acceptance criterion: consensus among maintainers. Where consensus is not
reached, the TSC decides by majority vote.

### Code of Conduct enforcement

The Code of Conduct is enforced by the maintainers. Serious reports are
escalated to the TSC.

## Adding and removing maintainers

- **Adding**: a maintainer proposes a new maintainer. Existing maintainers
  have 7 days to object. Silence is consent.
- **Removing for cause**: a majority of maintainers (excluding the subject
  of the action) may remove a maintainer for violations of the Code of
  Conduct or sustained neglect of responsibilities.
- **Stepping down**: maintainers may resign at any time by opening a PR
  against `CODEOWNERS`.

## Conflicts of interest

Contributors and maintainers must disclose any material conflicts of
interest that bear on their work in the project (for example, employment by
a company whose product competes with, or depends on, the emulator).

Disclosures are made in the PR or issue where they are relevant.

## Licensing

All contributions are accepted under [Apache 2.0](LICENSE). Contributors
apply a [DCO](https://developercertificate.org/) sign-off to every commit.

## Amendments

This document may be amended by RFC. Changes require TSC approval.
