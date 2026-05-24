# Documentation style guide

Authoritative conventions for docstrings, code comments, reference
docs, and the changelog. PRs deviate from these rules only with an
explicit one-paragraph rationale in the PR description.

## Core principle

**Code and reference docs describe the _current state_ of the
software. ADRs and the changelog describe its _history_.**

A docstring on a function in `src/` answers "what does this do
now?". A comment in `.lychee.toml` answers "why is this value set
this way?". Neither answers "how did we get here?" — that's the
job of `docs/adr/` and `CHANGELOG.md`.

A docstring that says "added in PR #50", "previously did X",
"calibrated against the v1.0.2 release cycle", or "see ADR 0038 for
the three options considered" is leaking history into the
present-tense surface. The reader needs to know the contract, not
its provenance.

## Docstrings

### Form

- First line: imperative-mood single sentence ending in a period.
  ("Return the resolved caller email.", not "Returns the resolved
  caller email." and not "This function returns…")
- Blank line.
- Optional body: present-tense factual prose describing the
  contract — inputs, outputs, edge cases, invariants. Use
  paragraphs for distinct concerns; reST field lists (`:param x:`,
  `:returns:`) only when generated tooling needs them.
- No headings, no bulleted "sections" inside docstrings.
- No trailing changelog-style notes.

### Allowed content

- What the function/class/module does, now.
- The contract's edge cases ("returns `None` when the row count is
  zero", "raises `ValidationError` on malformed input").
- Cross-references to **current** specifications: ADR numbers,
  BigQuery / DuckDB doc URLs, RFC numbers. The reference must be
  to a contract this code _conforms to_, not to a historical
  decision _about_ this code.

### Forbidden content

- Dates, version numbers, release names ("as of v1.0.2", "shipped
  May 2026").
- PR numbers, issue numbers, commit SHAs.
- Phase / wave / bucket labels (`P2.d`, `G4`, `Bucket A`, `slice-2`).
- "Previously X" / "we used to" / "this was added when" / "this
  closes".
- "TODO", "FIXME", "future work", "will eventually". If something
  is incomplete, either open a tracking issue and link it, or fix
  it now. A docstring is not a backlog.
- Author names ("originally written by X", "thanks to Y").
- Calibration narratives ("after observing 502s on PR #43, retries
  raised to 4 × 10s").

### Examples

❌ **Bad** (history + planning + PR references):

```python
def rewrite_session_user(bq_sql: str, caller: CallerIdentity) -> str:
    """Pre-translate BigQuery SQL for the ``SESSION_USER()`` function.

    Added in PR #50 (ADR 0038) to close the canonical
    RAP-via-SESSION_USER tenant-isolation pattern. Extended in
    PR #62 to also handle ``CURRENT_USER()`` and ``@@session.user``
    (ADR 0040). The Storage Read row_restriction path was previously
    failing to thread the caller through; that limitation was
    closed in the same PR.

    Returns the input unchanged when no rewrite is needed.
    Idempotent. TODO: handle ``SESSION_USER()`` inside SQL UDF
    bodies in a future release.
    """
```

✅ **Good** (present-tense contract):

```python
def rewrite_session_user(bq_sql: str, caller: CallerIdentity) -> str:
    """Substitute every BigQuery caller-identity call with a string literal.

    Handles ``SESSION_USER()``, ``CURRENT_USER()``, and
    ``@@session.user``. Each call site folds to the email returned
    by :func:`resolve_session_user` for ``caller``. The input is
    returned unchanged when no recognised spelling is present and
    when the SQL fails to parse. Idempotent: a second pass with
    the same caller is a no-op because the first pass replaced
    every matching node with a literal.

    See [ADR 0038](../../docs/adr/0038-session-user.md) for the
    resolution contract.
    """
```

### Module-level docstrings

Same rules. The module docstring describes what the module is for
right now — not its evolution. If a module collects routines from
multiple phases of work, say what they do as a coherent surface,
not when each was added.

### Test docstrings

Identical rules apply. Test docstrings describe what the test
verifies, not which PR introduced it or which CodeRabbit thread
prompted it.

## Code comments

### When to write one

Only when the **why** is non-obvious from the code. Never the
**what** — well-named identifiers cover that.

### Allowed content

- A subtle invariant ("DuckDB returns this as a list even for
  single-row results; unwrap deliberately").
- A specific workaround for an upstream bug ("workaround for
  DuckDB issue #12345 — remove when the fix lands").
- A non-obvious performance choice with measurement.

### Forbidden content

Same list as docstrings: dates, PRs, phases, "previously", "added
in".

### Example

❌ **Bad**:

```python
# Bumped from 5 to 10 after the v1.1.0 cascade — Google Cloud
# docs CDN added a locale-prefix hop in May 2026 that ran us out
# of redirect budget. PR #63 raised the cap.
max_redirects = 10
```

✅ **Good**:

```python
# Google Cloud docs URLs chain through CDN + locale-prefix
# redirects (typically 6–8 hops). Bumping beyond this risks
# masking infinite-loop CDNs.
max_redirects = 10
```

The good version answers "why this number" without timestamping
the answer.

## Reference docs

`docs/reference/**` and `docs/architecture/**` (except
`docs/adr/**` and `docs/rfcs/**`) describe how the software
behaves today. Apply the docstring rules: no history, no PR
references, no phase labels.

Auto-generated reference docs (`conformance-coverage-matrix`,
`compatibility-matrix`, `sql-function-mapping`, `api-coverage`)
are governed by their generator scripts; the same rules apply to
those generators.

## ADRs

ADRs in `docs/adr/**` are exempt from these rules — historical
decision records are exactly what the rules above push out of the
code. An ADR's job is to record what was decided, why, and what
was considered. They are immutable once accepted; a later decision
that supersedes an earlier one ships as a new ADR that explicitly
supersedes it.

## CHANGELOG

The changelog follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) +
[Common Changelog](https://common-changelog.org/). Both are
explicit specifications; deviations require justification.

### Structure

```markdown
# Changelog

## [X.Y.Z] - YYYY-MM-DD

### Changed
- Single-line imperative-mood entry, capitalised, period-terminated.

### Added
- One change per bullet.

### Removed
- Same.

### Fixed
- Same.
```

- Sections in the order **Changed / Added / Removed / Fixed**
  (Common Changelog ordering; breaking changes most important).
- Optional `### Deprecated` and `### Security` per Keep a Changelog
  if the version actually has such entries.
- One bullet per change. No sub-headings inside a version section.
- No paragraphs inside entries. If a change needs explanation
  beyond what fits in one line, that explanation belongs in an
  ADR or in the docstring of the code it describes — link to it,
  don't restate it.

### Entry form

- Imperative mood. ("Add CURRENT_USER alias for SESSION_USER",
  not "Added CURRENT_USER alias" and not "This release adds…")
- Capitalised first letter, period-terminated.
- Reference link at the end is optional and discouraged. The
  CHANGELOG is read for what changed, not who did it.

### Forbidden in CHANGELOG entries

- Fixture counts, PR numbers, file paths, ADR numbers, phase
  labels, recorder narratives, contributor names.
- Multi-paragraph essays.
- Per-entry sub-headings (`**Implementation:**`,
  `**Coverage:**`, `**Out of scope:**`).
- "This PR" / "this change" — entries are about _what changed_,
  not _what work happened_.
- TODOs / planned follow-up.

### Examples

❌ **Bad** (current repo style):

```markdown
- **`CURRENT_USER()` + `@@session.user` + Storage Read
  `row_restriction` caller threading** (ADR 0040). Closes three
  items deferred by ADR 0038's out-of-scope section in a single
  follow-up:

  1. **`CURRENT_USER()` function alias** — BigQuery documents…
  2. **`@@session.user` system-variable spelling**…
  3. **Storage Read `row_restriction` caller threading**…

  Coverage:
  - **8 new unit tests** in `tests/unit/sql/rewriter/…`
  - **1 new integration test** in `tests/integration/…`
  - **6 new e2e tests** (2 per client × Python / Node.js / Go / Java)…
```

✅ **Good**:

```markdown
### Added
- Recognise `CURRENT_USER()` and `@@session.user` as aliases for `SESSION_USER()`.
- Thread the caller identity into Storage Read row-restriction filters.
```

The reader who wants more depth reads the corresponding ADR. The
ADR is the durable place for "three options considered, here's
why we picked C". The changelog is for "here's what changed".

### Authoring cadence

CHANGELOG entries are authored **at release time**, not on every
PR. A PR's body documents what the PR did; the changelog
captures what the release shipped. At release time the operator
reads `git log <prev-tag>..HEAD` and synthesises one bullet per
user-visible change.

There is no `## [Unreleased]` section between releases. The first
heading after `# Changelog` is always the most recent shipped
version.

### Mutability

A released version's section is **immutable** except to fix an
error (typo, wrong reference, factual mistake about what
shipped). Mutability is not for adding context, expanding
explanations, or back-filling work that was forgotten — that
goes in a new release.

## Migration path for existing content

This guide ships as the authoritative standard. Existing
docstrings, comments, and the CHANGELOG were authored before it
and will be brought into compliance through a series of focused
PRs:

- A separate PR rewrites `CHANGELOG.md` end-to-end (every version
  section + the removal of the `Unreleased` section).
- A separate PR sweeps every docstring under `src/bqemulator/**`.
- A separate PR sweeps `docs/reference/**` and
  `docs/architecture/**` (excluding ADRs and RFCs).

Until those PRs land, expect old code to violate this guide. New
PRs against `main` are expected to comply.
