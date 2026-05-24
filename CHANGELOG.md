# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entry types:

- **Added** — new features
- **Changed** — changes to existing behavior
- **Deprecated** — features slated for removal (with removal timeline)
- **Removed** — features that have been removed (must have been deprecated
  for at least 2 MINOR versions or 6 months)
- **Fixed** — bug fixes
- **Security** — security fixes or relevant notices

Each PR that changes observable behavior MUST add an entry under `Unreleased`.
On release, `scripts/changelog.py X.Y.Z` moves those entries into a new version
section and adds the release date.

## [Unreleased]

### Added

- **INFORMATION_SCHEMA conformance corpus — 18 fixtures recorded
  + two G4 rewriter bug fixes**. The G4 rewriter implementation
  (2026-05-21 work) covering ``SCHEMATA`` / ``TABLES`` /
  ``COLUMNS`` / ``TABLE_OPTIONS`` / ``VIEWS`` / ``PARTITIONS``
  shipped with the fixture *queries* but no recorded baselines;
  this PR records all 18 against real BigQuery, exposing two
  pre-existing rewriter bugs that are now closed:

  - **Stray trailing backtick** — every ``_build_patterns``
    regex matched a backtick-quoted reference like
    ``` `dataset.INFORMATION_SCHEMA.TABLES` ``` but didn't
    consume the closing ``` ` ```, leaving a stray backtick in
    the rewritten SQL that broke the downstream SQLGlot
    tokeniser. Added ``` `? ``` after each ``{view}`` pattern.
  - **Bare-`NULL` columns in the empty-rows path** — when the
    matched view's catalog state was empty, the rewriter
    emitted ``(VALUES (NULL, NULL, …, NULL) … WHERE FALSE)``.
    DuckDB inferred every NULL column as ``INTEGER``, so the
    wire schema showed ``schema_name: INTEGER`` etc. instead
    of the BigQuery-documented types. Refactored each of the
    six empty-row helpers to emit
    ``CAST(NULL AS STRING) AS catalog_name, CAST(NULL AS TIMESTAMP) AS creation_time, …``
    with a per-view ``_<VIEW>_COLUMN_TYPES`` tuple driving the
    types (STRING / TIMESTAMP / INTEGER per BigQuery's docs).

  **Third bug fix landed in the same PR** — DDL ``NOT NULL``
  constraints now round-trip into ``TableFieldSchema.mode``. The
  pre-fix ``ddl_sync._introspect_schema`` hard-coded every column
  to ``mode="NULLABLE"`` regardless of the DDL because DuckDB's
  Arrow exporter always emits ``nullable=True``. The fix
  cross-references DuckDB's ``PRAGMA table_info`` (which
  preserves the ``notnull`` flag) and routes that into the
  ``REQUIRED``/``NULLABLE`` mode the BigQuery
  INFORMATION_SCHEMA.COLUMNS ``is_nullable`` column reads from.
  The ``api/routes/jobs.py:_schema_from_create_table`` preview
  helper also now reads the SQLGlot ``NotNullColumnConstraint``
  AST so the REST job-preview path is consistent.

  **Fourth fix in the same PR** — ``CREATE TABLE`` DDL extras
  now flow into ``TableMeta``. A new ``_extract_ddl_metadata``
  helper in ``ddl_sync`` parses the SQLGlot ``Create`` AST and
  populates:

  - ``time_partitioning.field`` from ``PARTITION BY <col>``
    (``_PARTITIONDATE`` / ``_PARTITIONTIME`` pseudo-columns are
    excluded per BigQuery's ingestion-time contract where
    ``field`` is None).
  - ``description`` from ``OPTIONS(description="…")``.
  - ``time_partitioning.require_partition_filter`` from
    ``OPTIONS(require_partition_filter=TRUE)``.
  - ``time_partitioning.expiration_ms`` from
    ``OPTIONS(partition_expiration_days=N)``.

  The TABLE_OPTIONS rewriter was already structured to project
  from these ``TableMeta`` fields — populating them at DDL-sync
  time closes the TABLE_OPTIONS fixtures end-to-end.

  Fixture state (17 PASS + 1 XFAIL):

  - **SCHEMATA** (3 / 3 PASS), **TABLES** (3 / 3 PASS),
    **VIEWS** (3 / 3 PASS)
  - **COLUMNS** (2 PASS + 1 XFAIL) — `is_columns_basic` ✅,
    `is_columns_partitioning_column` ✅,
    `is_columns_with_struct_field` ❌ (only remaining gap —
    catalog flattens STRUCT/ARRAY nested fields; BigQuery emits
    a row per nested field. Closure requires recursive schema
    flattener in the COLUMNS emitter; separate workstream.)
  - **TABLE_OPTIONS** (3 / 3 PASS) — all closed by the DDL
    extras extraction.
  - **PARTITIONS** (3 / 3 PASS) — `is_partitions_ingestion_time`,
    `is_partitions_basic`, `is_partitions_empty_table` all
    materialised from existing partition_state.

  Each XFAIL carries a ``KNOWN_DIVERGENCES`` entry pointing at
  the specific catalog-state gap that needs to close; each is
  its own future workstream sized similarly to the original G4
  work and tracked under [ADR 0022](docs/adr/0022-conformance-corpus-design.md)
  §1.

  Coverage-matrix regenerated: corpus fixture count grows by 18
  (1141 → 1159); the INFORMATION_SCHEMA category lifts from
  **0 / 6 covered** to **6 / 6 covered** (with varying depth — 4
  views fully green, 2 partially XFAILed). Recording cost: ~$0
  against ``perigon-health-nonprod-svc`` (18 queries × 10 MiB
  minimum scan).

### Changed

- **Lychee linkcheck resolves repo-self-references locally** —
  closes a structural failure mode observed during the v1.1.0
  cascade. Generated docs (coverage-matrix, compat-matrix,
  function-mapping, api-coverage) emit absolute
  ``github.com/jjviscomi/bqemulator/{blob,tree}/main/<path>`` URLs
  for every repo-internal file reference — necessary for the
  mkdocs-rendered public site, but lychee on a PR branch would
  fetch those URLs over HTTP, get a 404 (file not yet on
  ``main``) or a transient github.com 502, and fail the build.
  Every PR adding new files would hit this; PRs #57, #58, and
  #60 each failed linkcheck 1-4 times during the cascade.

  Fix: invoke lychee with two ``--remap`` arguments rewriting
  ``github.com/jjviscomi/bqemulator/{blob,tree}/main/(.+)`` to
  ``file://<workspace>/<path>``. lychee resolves the
  rewritten URL against the PR-branch checkout instead of
  fetching ``main``. The remap is CLI-side (not in
  ``.lychee.toml``) because lychee's config-file ``remap``
  expects valid URLs and can't expand the absolute workspace
  path. Wired in two places:

  - [``.github/workflows/linkcheck.yml``](.github/workflows/linkcheck.yml)
    — uses ``${{ github.workspace }}`` template var.
  - [``Makefile``](Makefile) ``linkcheck`` target — uses
    ``$(CURDIR)`` for local-dev parity (``$$`` in the recipe
    expands to a literal ``$``, so the shell receives
    ``$(CURDIR)`` which make substitutes; the trailing ``$$1``
    is similarly the make-escape that produces the literal
    ``$1`` lychee needs as its regex backreference).

  Behavioural envelope vs. pre-fix:
  - PR-branch new files: now pass instead of 404 (correct).
  - github.com 502 transients on self-links: now no-op (correct).
  - Renamed/deleted files: still caught (lychee reports
    "file not found" against the local checkout — equivalent
    signal to the previous 404).
  - The mkdocs public site keeps fetching the absolute github
    URLs at render time — ``--remap`` is a lychee-only directive.

  Verified locally: ``make linkcheck`` → 2345 total, 0 errors,
  10s. The ``.lychee.toml`` retains the full design rationale
  as a top-level comment block.

### Added

- **TPC-DS chunk 1 — 11 "easier" fixtures recorded** (59 → 70
  TPC-DS coverage). Closes the first slice of the expansion plan
  documented in
  [`docs/architecture/contributing/tpcds-expansion-plan.md`](docs/architecture/contributing/tpcds-expansion-plan.md).
  Queries added: **q12, q20, q37, q45, q57, q65, q79, q81, q82,
  q93, q98**. Per-fixture characteristics:

  - **q12 / q20 / q98** — window ``SUM`` over ``PARTITION BY
    i_class``: web / catalog / store channel revenue-ratio.
  - **q37 / q82** — item × inventory × date_dim × catalog/store_sales
    join with manufacturer / inventory range filters.
  - **q45** — web_sales × customer × customer_address with
    ``SUBSTR(ca_zip, 1, 5) IN list OR i_item_id IN subquery``
    disjunction.
  - **q57** — AVG/RANK window over (i_category, i_brand, cc_name)
    by call-center month (simplified single-CTE variant of the
    full 3-CTE Q57; exercises the same AVG-OVER-window primitives
    the spec targets).
  - **q65** — DENSE_RANK + low-performer ``revenue ≤ 0.1 × AVG``
    store-item filter.
  - **q79** — household_demographics + customer-display join with
    ``hd_dep_count = 6 OR hd_vehicle_count > 2`` disjunction.
  - **q81** — customer_total_return CTE + ``> 1.2 × AVG``
    correlated subquery (same shape as Q1 but with web-side
    address-fragment projection).
  - **q93** — store_sales ⋈ store_returns ⋈ reason with
    ``CASE WHEN sr_return_quantity IS NOT NULL`` net-amount
    rewrite.

  Each fixture seeds 1-5 rows per dimension table and returns
  1-5 result rows (deterministic against BigQuery's recording).
  Recorder runs cost a combined ~$0 against
  ``perigon-health-nonprod-svc`` (US multi-region; total
  ~7 KiB scanned across all 11). All 11 PASS the in-process
  replay on first run (``pytest tests/conformance -m conformance
  -k 'tpcds_q12 or … or tpcds_q98'`` → 11 passed in 4.3s) —
  confirming the existing 92-rule translator already supports
  every construct these queries use. No new SQL rules added.

  [Coverage-matrix](docs/reference/conformance-coverage-matrix.md)
  regenerated: corpus fixture count 1141 → 1152; TPC-DS family
  count 59 → 70 (4 chunks remaining, ~$0.04 total against
  ``perigon-health-nonprod-svc`` to complete the full
  99-query corpus).

- **`CURRENT_USER()` + `@@session.user` + Storage Read
  `row_restriction` caller threading** (ADR 0040). Closes three
  items deferred by ADR 0038's out-of-scope section in a single
  follow-up:

  1. **`CURRENT_USER()` function alias** — BigQuery documents
     `CURRENT_USER()` as a co-equal spelling of `SESSION_USER()`.
     The pre-translator now matches both `exp.SessionUser` and
     `exp.CurrentUser` nodes; both resolve via the same
     `resolve_session_user(caller)` helper (no new resolution
     logic).
  2. **`@@session.user` system-variable spelling** — SQLGlot
     parses this as `Dot(Parameter(Parameter(Var('session'))),
     Identifier('user'))`. A new helper
     `_is_session_user_system_var` pattern-matches the exact AST
     shape (not the rendered SQL) to avoid false-positive
     matches on user-defined columns named `user` reached via
     an unrelated parameter expression.
  3. **Storage Read `row_restriction` caller threading** —
     pre-closure, `grpc_api/read_servicer._build_read_sql`
     translated the user-supplied `row_restriction` without a
     caller, so any caller-identity function inside the filter
     folded to the `ANONYMOUS_CALLER` sentinel regardless of the
     `X-Bqemu-Caller` header. Hoisted the caller resolution
     above `_build_read_sql` and threaded `caller` through to
     the inner `translator.translate(..., caller=caller)` call.
     The second row-restriction path (BigQuery-shaped variant
     for the row-access policy rewriter) already received the
     caller via existing plumbing at line 252 — no change there.

  Coverage:

  - **8 new unit tests** in
    `tests/unit/sql/rewriter/test_session_user.py` pin the three
    new code paths (bare + lower-case + unauthenticated +
    RAP-filter shape for each spelling + the `SELECT user FROM
    users` false-positive guard + all-three-spellings-in-one-
    query).
  - **1 new integration test** in
    `tests/integration/test_storage_read_edge_cases.py` exercises
    a Storage Read `row_restriction` of the form
    `owner = SESSION_USER()` with an `X-Bqemu-Caller` gRPC
    metadata header. Pre-closure: every row filtered out;
    post-closure: exactly the calling user's row returned.
  - **8 new e2e tests** (2 per client × Python / Node.js / Go /
    Java SDKs) cover `SELECT CURRENT_USER()` and
    `SELECT @@session.user` through the official client
    libraries against a live container. `bq` CLI is skipped per
    ADR 0038's existing rationale (the CLI doesn't set
    `X-Bqemu-Caller`).

  Surface inventory updated: `CURRENT_USER` joins `SESSION_USER`
  and `GENERATE_UUID` in the non-deterministic family
  (excluded from the corpus per ADR 0022 §1.2; covered at the
  unit + e2e tiers). The `SESSION_USER` note's Storage Read
  caveat is updated to reflect the closure.

  Out of scope (preserved from ADR 0038): `SESSION_USER()` /
  `CURRENT_USER()` inside a SQL UDF body — UDFs are
  pre-translated at definition time when no caller exists.
  Closing this requires a UDF-rewrite-at-call-time pass
  scope-comparable to ADR 0038's original work; deferred.

  See [ADR 0040](docs/adr/0040-session-user-coverage-closure.md)
  for the full decision record.

- **TPC-DS expansion plan documented** — new
  [`docs/architecture/contributing/tpcds-expansion-plan.md`](docs/architecture/contributing/tpcds-expansion-plan.md)
  tracks the planned 59 → 99 TPC-DS coverage expansion. Lists the 40
  missing queries in numerical order with per-query complexity
  hints (table count + key SQL feature), documents the per-fixture
  authoring recipe, BigQuery adaptation patterns from the TPC-DS
  reference SQL, seed-data sizing rules, cost guardrails (~$0.01
  total for all 40 against an operator-supplied GCP project), and
  the three resolved scope questions (include all 40, cost de
  minimis, no periodic re-record cadence). No fixtures
  recorded in this PR; the plan is the durable artefact so the work
  survives session boundaries. `docs/architecture/testing-strategy.md`
  updated to reference the plan. No new SQL rules are anticipated —
  every missing query uses features already supported by the
  92-rule translator (verified against the existing 59-fixture
  subset).

- **SLSA Build Provenance attestations on GitHub Release assets**
  (ADR 0039). Closes the ``Signed-Releases`` gap OpenSSF Scorecard
  flagged after the v1.0.2 release: the workflow now runs
  ``actions/attest-build-provenance@a2bbfa25 # v4.1.0`` against
  ``dist/*`` in the ``github-release`` job of
  ``.github/workflows/release.yml``, produces a ``.intoto.jsonl``
  SLSA v1.0 Build Provenance bundle, and uploads the bundle to the
  GitHub Release alongside the wheel + sdist. Consumers verify
  with ``gh attestation verify <file> --owner jjviscomi``.
  ``.github/workflows/docker.yml``'s existing
  ``attest-build-provenance`` call also bumped — from floating
  ``@v1`` (SLSA v0.2 schema) to the same SHA-pinned ``@v4.1.0``
  (SLSA v1.0 schema) for cross-workflow consistency. Both call
  sites are now full-commit-SHA-pinned per OpenSSF Scorecard's
  strict ``Pinned-Dependencies`` reading (Scorecard gives full
  credit for commit-SHA even on first-party ``actions/*``;
  AGENTS.md's relaxed-major-tag rule for ``actions/*`` was a
  pragmatic compromise, not the ceiling). PyPI's own sigstore
  attestations via Trusted Publishing are preserved unchanged —
  the GitHub-Release attestation is the GitHub-visible parallel,
  not a replacement. Expected Scorecard
  ``Signed-Releases`` score trajectory: 0/10 today → 2/10 after
  v1.1.0 → 10/10 after v1.1.4 (the check inspects the last 5
  releases; tags are immutable so prior releases can't be
  retroactively attested). Composite Scorecard score lift ~+1.5
  immediately, ~+2.5 by v1.1.4.

- **`SESSION_USER()` SQL function + canonical RAP-via-SESSION_USER
  e2e coverage** (ADR 0038). The function was documented in the
  surface inventory but had zero implementation and zero tests —
  the rendered conformance-coverage matrix's "Exercised at the unit
  tier" claim was aspirational. This PR makes it true.

  Implementation: a new ``rewrite_session_user`` pre-translator
  walks the SQLGlot AST and replaces every ``SessionUser`` node with
  a string literal of the resolved caller email, before SQLGlot's
  BigQuery → DuckDB transpile. Caller identity is threaded through
  ``SQLTranslator.translate`` via a new optional ``caller`` kwarg;
  five call sites updated to pass the ``CallerIdentity`` they
  already construct (``jobs/executor.py``, three sites in
  ``scripting/interpreter.py``, one in
  ``grpc_api/read_servicer.py``). DuckDB's native ``SESSION_USER``
  resolves to the literal ``'duckdb'`` — pre-translator
  substitution is what prevents a confusing
  ``SELECT SESSION_USER()`` → ``'duckdb'`` regression.

  Resolution contract (per ADR 0038):
  - ``user:<email>`` / ``serviceAccount:<email>`` / ``group:<email>``
    / ``domain:<host>`` → strip prefix, return bare email/host.
  - ``allUsers`` / ``allAuthenticatedUsers`` / unknown shape →
    raw principal string passthrough (defensive only — these are
    grantee-side identifiers, never caller identifiers).
  - Unauthenticated fallback (``is_authenticated=False``) → the
    literal ``"anonymous"`` sentinel, so RAP filters comparing
    ``SESSION_USER()`` against a tenant key safely deny every row.

  Coverage:
  - **21 unit tests** in ``tests/unit/sql/rewriter/test_session_user.py``
    pin the resolver contract per IAM-member shape + the rewriter's
    AST walk (multiple call sites, idempotent re-runs, fast-path
    no-op, lower-case spelling, string-literal-not-rewritten,
    unparseable-SQL passthrough, view-body substitution).
  - **5 new integration tests** in
    ``tests/integration/test_row_access_policies.py`` exercise the
    canonical
    ``REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id`` RAP
    filter through the in-process emulator with two callers + the
    unauthenticated fallback + a service-account caller.
  - **4 new e2e test files** (Python, Node.js, Go, Java) cover the
    same RAP filter pattern through the official client libraries
    against a live container. ``bq`` CLI is skipped per the
    existing module docstring (the CLI doesn't set
    ``X-Bqemu-Caller``); the skip rationale was extended to point
    at the new e2e files.

  Out of scope (documented in ADR 0038):
  - ``CURRENT_USER`` and ``@@session.user`` (deprecated /
    system-variable spellings of the same function).
  - ``SESSION_USER()`` inside a SQL UDF body — UDFs are
    pre-translated at definition time when no caller exists; the
    function inside a UDF body folds to ``"anonymous"`` permanently.
  - The Storage Read filter pre-pass at
    ``grpc_api/read_servicer.py:122`` (``_build_filter_sql`` for the
    ``row_restriction`` field) doesn't yet receive caller context —
    folds to ``"anonymous"`` regardless of the actual caller.
    The canonical SESSION_USER use is RAP filters, not Storage Read
    row_restriction; documented as a known limitation.

  See [ADR 0038](docs/adr/0038-session-user.md) for the full
  decision record, the three implementation options considered, and
  the unauthenticated-fallback rationale.

- **OpenSSF Scorecard workflow + public badge.** New
  `.github/workflows/scorecard.yml` runs the official
  `ossf/scorecard-action` against `main` on every push, every
  published release, weekly via cron, and on `workflow_dispatch`.
  The score (0–10) publishes to the OSSF public database at
  `https://api.securityscorecards.dev/projects/github.com/jjviscomi/bqemulator`
  (opt-in: `publish_results: true`) and surfaces in the README as a
  badge linking to the public viewer. Scorecard's SARIF output also
  uploads to GitHub's Security tab via
  `github/codeql-action/upload-sarif` so findings show alongside
  CodeQL alerts. Third-party action pinning follows the
  project-wide OpenSSF-alignment rule: SHA pin + trailing
  `# vX.Y.Z` comment (ossf/scorecard-action@4eaacf0 # v2.4.3,
  github/codeql-action/upload-sarif@7211b7c # v4.36.0). Initial
  expected score ~8/10; the two checks that drag are
  CII-Best-Practices (not enrolled) and Contributors (single
  maintainer) — actionable checks (Pinned-Deps, Signed-Releases,
  Branch-Protection, SAST, Code-Review) all score high. The
  public database takes ~24-48h to populate the first score; the
  badge endpoint returns 404 until then. ``scripts/bump_version.py``
  was extended with `_README_SCORECARD_BADGE_RE` so each release
  also rewrites the Scorecard badge URL's ``?v=X.Y.Z`` cache-bust
  suffix (camo TTL ~24h; the bump forces an immediate refresh).
  See [ADR 0037](docs/adr/0037-openssf-scorecard.md) for the
  decision context, expected check breakdown, and trigger
  rationale.

- **Non-blocking code-quality gates: complexity, duplication, dead code.**
  Three concerns that the existing pre-commit chain (ruff + mypy +
  bandit + pip-audit + interrogate + typos) doesn't meaningfully
  enforce today now have observable CI signal:
  - **`radon` + `xenon`** for per-function cyclomatic complexity
    (`make quality-complexity`). Baseline thresholds picked against
    the v1.0.2 codebase — `--max-absolute E --max-modules C --max-average A` —
    so today's code passes; regression past those tiers fails. Fills
    the gap left by ruff's intentionally-suppressed ``C901`` /
    ``PLR0911`` / ``PLR0912`` / ``PLR0913`` (type-dispatch functions
    are naturally branchy; xenon caps the worst case absolutely
    without requiring per-function `noqa`).
  - **`jscpd`** for cross-file DRY violations
    (`make quality-duplication`). The one category nothing in the
    stack covered. Threshold 1.0% — v1.0.2 baseline 0.36% (7 clones,
    112 lines, all 11–22 lines and structurally template-shaped).
    Wired via `npx -y jscpd@4` (major-version pin for reproducibility)
    so the Python project takes on no permanent JS dep.
  - **`vulture`** wired into the new `make quality-dead-code`
    target. The dev-dep + `[tool.vulture]` config already existed;
    it was never invoked. New `.vulture_whitelist.py` documents the
    one current false positive (the reserved `use_cache` kwarg on
    `execute_query_job`). Each future whitelist entry lands via PR
    review only — that's the contract that keeps the gate
    trustworthy.

  All three run via a single `make quality` umbrella and the new
  `.github/workflows/code-quality.yml` workflow (per-PR + push-to-main
  + nightly + manual). Every step uses `continue-on-error: true` —
  the gates are **non-blocking by design** for this PR. `make verify`
  is unchanged. Promote-to-required is a separate follow-up PR once
  the thresholds settle against `main`. See
  [ADR 0035](docs/adr/0035-code-quality-gates.md) for the full design,
  baselines, and follow-up checklist.

### Changed

- **All GitHub Actions + Dockerfile base images SHA-pinned**
  (OpenSSF Scorecard `Pinned-Dependencies` sweep). The
  pre-sweep audit found 113 `uses:` references across 14 workflow
  files using floating tags — first-party `actions/*` were
  previously exempt from SHA-pinning per AGENTS.md's pragmatic
  compromise, and a few third-party pins (``softprops/action-gh-release@v2``,
  ``pypa/gh-action-pypi-publish@release/v1``, ``actions/cache@v4``,
  ``docker/setup-buildx-action@v3``, ``docker/build-push-action@v7``,
  ``coursier/setup-action@v3``) had drifted from policy. Both
  classes now pin to the latest patch within their current major
  version, with trailing ``# vX.Y.Z`` comments for Dependabot
  upgrade-anchor compatibility (SHAs below are abbreviated to the
  first 8 hex characters for readability; the actual workflow-file
  references are full 40-character commit SHAs per the policy
  documented in [AGENTS.md](AGENTS.md)):

  - ``actions/checkout`` v4 → ``@34e11487 # v4.3.1``
  - ``actions/setup-python`` v5 → ``@a26af69b # v5.6.0``
  - ``actions/setup-node`` v6 → ``@48b55a01 # v6.4.0``
  - ``actions/setup-go`` v5 → ``@40f1582b # v5.6.0``
  - ``actions/setup-java`` v5 → ``@be666c2f # v5.2.0``
  - ``actions/cache`` v4 → ``@0057852b # v4.3.0``
  - ``actions/upload-artifact`` v4 → ``@ea165f8d # v4.6.2``
  - ``actions/download-artifact`` v8 → ``@3e5f45b2 # v8.0.1``
  - ``coursier/setup-action`` v3 → ``@fd1707a7 # v3.0.0``
  - ``docker/setup-buildx-action`` v3 → ``@8d2750c6 # v3.12.0``
    (consistency with the existing pin in ``docker.yml``)
  - ``docker/build-push-action`` v7 → ``@f9f30427 # v7.2.0``
    (same)

  ``Dockerfile``'s two ``FROM python:3.14-slim-bookworm`` lines
  also gain a ``@sha256:a9bee15510a3641…`` digest pin per the
  OCI multi-arch index for the May 20 2026 push. Dependabot's
  ``docker`` ecosystem updater bumps both the tag and digest
  together on each upstream release.

  ``AGENTS.md``'s "GitHub Actions pinning" section updated to
  reflect the post-sweep policy: **every** ``uses:`` reference is
  SHA-pinned (no first-party exemption). The relaxed-actions/*
  rule was a pragmatic compromise based on the smaller threat
  model for GitHub-owned actions — but Scorecard scores full
  credit only for commit-SHA pins regardless of action provenance,
  and there's no operational cost to extending the rule (Dependabot
  handles both alike).

  Expected Scorecard impact: ``Pinned-Dependencies`` lifts from
  ~4-5/10 (partial credit for major-tag pins) to ~9-10/10 on next
  weekly run. Composite score lift ~+1 point.

  Three actions remain un-pinned at this commit, all covered by
  the in-flight PR #57 (artifact attestations + SHA-pin sweep):
  ``softprops/action-gh-release@v2``,
  ``pypa/gh-action-pypi-publish@release/v1``,
  ``actions/attest-build-provenance@v1`` (in ``docker.yml``).

- **Workflow ``permissions:`` scoped to least-privilege**
  (OpenSSF Scorecard ``Token-Permissions`` sweep). Pre-sweep audit
  found five workflows with over-broad top-level permissions:

  - ``linkcheck.yml`` — no top-level ``permissions:`` block at all
    (defaulted to repo-wide). Added ``contents: read``.
  - ``conformance.yml`` — top-level ``issues: write`` was carry-over
    from an earlier "post issue on failure" pattern that's no
    longer wired. Removed.
  - ``docs.yml`` — top-level ``contents: write`` covered both the
    PR-time ``build`` job (read-only) and the post-merge ``deploy``
    job. Moved ``contents: write`` to the ``deploy`` job only;
    top-level is now ``contents: read``.
  - ``docker.yml`` — top-level ``packages: write``,
    ``id-token: write``, ``attestations: write`` covered just the
    one ``publish`` job. Top-level now ``contents: read``; the
    writes moved to ``publish``'s ``permissions:`` block.
  - ``release.yml`` — top-level ``contents: write``,
    ``id-token: write``, ``attestations: write`` covered three
    jobs (``build``, ``publish-pypi``, ``github-release``) with
    different needs. Top-level now ``contents: read``; each job
    gets exactly the writes it uses:
    - ``build`` → ``contents: read`` (just checkout + upload-artifact).
    - ``publish-pypi`` → ``contents: read`` + ``id-token: write``
      (PyPI Trusted Publishing OIDC).
    - ``github-release`` → ``contents: write`` (release upload) +
      ``id-token: write`` + ``attestations: write`` (the SLSA
      Build Provenance step from ADR 0039).

  The other eleven workflows (``ci.yml`` / ``chaos.yml`` /
  ``code-quality.yml`` / ``codeql.yml`` / ``differential.yml`` /
  ``e2e.yml`` / ``examples.yml`` / ``fuzz.yml`` / ``mutation.yml``
  / ``perf.yml`` / ``scorecard.yml``) were already minimal — no
  changes needed.

  Expected Scorecard ``Token-Permissions`` lift: low score → ~9-10/10
  on next weekly run (every workflow now has a top-level read-only
  ``permissions:`` declaration with writes scoped per-job).

- **Python example requirements tightened to CVE-clean floors.** The
  OpenSSF Scorecard `Vulnerabilities` check (added in PR #48) reported
  ~100 historical PYSEC / GHSA IDs against the `docs/examples/python/`
  projects' wide `>=X.Y` lower bounds — OSV-scanner treats any version
  inside a declared range as a candidate match, so `apache-airflow>=2.8`
  intersected every airflow CVE from 2.8.0 onward even though pip
  resolves the latest 2.x at install time. Tightening lower bounds to
  the highest version with no open OSV records inside the existing
  upper bound (verified via `osv-scanner` 2.2.4 → "0 issues"):
  - `docs/examples/python/airflow-dag-test/requirements.txt`:
    `apache-airflow>=2.8` → `>=2.11.1`,
    `apache-airflow-providers-google>=10.0` → `>=11.0`,
    `pytest>=8.0,<9.0` → `>=9.0.3,<10.0`.
  - `docs/examples/python/pyspark-bigquery/requirements.txt`:
    `pyspark>=3.5` → `>=3.5.2`,
    `pyarrow>=14.0` → `>=17.0`.
  - `docs/examples/python/pytest-integration/requirements.txt`:
    `flask>=3.0` → `>=3.1.3`,
    `pytest>=8.0,<9.0` → `>=9.0.3,<10.0`.
  Each requirements.txt now carries an inline comment naming the
  specific GHSA / PYSEC IDs the new floor closes (so future audits
  don't need to re-derive the rationale). The emulator's own
  `pyproject.toml` runtime deps already pin tight CVE floors via the
  existing transitive-pin block (`cryptography>=46.0.7`,
  `pyjwt>=2.12.0`, `urllib3>=2.7.0`, etc.) — no main-project bump
  needed; ``pip-audit`` against the runtime closure was already clean.

- **`github/codeql-action` pinned by full commit SHA** in
  `.github/workflows/codeql.yml`. Previously used floating
  `@v4` major tags for `init` and `analyze`, which AGENTS.md's
  OpenSSF-alignment rule already prohibited for non-first-party
  actions; surfaced as pre-existing tech debt in PR #48's
  description (ADR 0037). Now pinned to
  `github/codeql-action/{init,analyze}@7211b7c8077ea37d8641b6271f6a365a22a5fbfa # v4.36.0`
  matching the `upload-sarif` pin the Scorecard workflow uses.
  Bumps the OpenSSF Scorecard `Pinned-Dependencies` check on the
  initial-publish run so the public score starts higher rather
  than dragging in a follow-up. Dependabot already monitors
  `.github/workflows/*.yml` so the SHA moves forward
  automatically.

- **lychee retry budget bumped** in `.lychee.toml` — `max_retries` 2→4,
  `retry_wait_time` 3s→10s. Calibrated against the v1.0.2 release CI
  cycle (PR #43, 2026-05-23) where lychee hit repeated `502 Bad Gateway`
  from `github.com/.../blob/main/...` source-tree URLs and the
  prior 2×3s retry budget couldn't outlast the transient window.
  Four retries × 10s give a ~40-second total budget per failing URL,
  which absorbs the typical 30-60s blob-render transient without
  masking real outages (adding `502` to the `accept` list was
  rejected — a renamed repo would 502 persistently and we want to
  know).

- **Cyclomatic-complexity gate ratcheted from rank E to rank C +
  promoted to required.** The `quality-complexity` gate introduced by
  ADR 0035 (non-blocking, `xenon --max-absolute E`) now enforces
  `xenon --max-absolute C --max-modules C --max-average A` on
  `src/bqemulator/`. Every function must rank C or better
  (cyclomatic complexity ≤ 20); no exclusions. Ten D-rank and
  E-rank functions surfaced by the baseline audit were refactored
  using two patterns documented in ADR 0036: dispatch-table for
  type-keyed branching (the arrow_bridge / avro_serializer /
  classify_statement_type / scripting parser cases) and helper
  extraction for procedural sub-blocks (the catalog cascade /
  interval parser / write-append post-processor / table-meta
  builder). Worst-case complexity drops from 39 (`_format_bq_value`)
  to 14. The gate is now part of `make verify`, and
  `.github/workflows/code-quality.yml`'s complexity step drops
  `continue-on-error: true`. The branch-protection ruleset's
  required-checks list will be updated to include the `Quality
  gates` job (stable name across future blocking-status changes)
  in a follow-up step once CI on this PR reports the new check
  name green — adding it pre-CI would create a chicken-and-egg
  merge block. Duplication
  (jscpd) + dead-code (vulture) gates stay non-blocking — each gets
  its own ratchet PR when its baseline settles. See
  [ADR 0036](docs/adr/0036-complexity-ratchet-to-c.md) for the full
  audit table, per-function refactor results, and the bucket-A /
  bucket-B refactor patterns.

- **`scripts/bump_version.py` now bumps the README's shields.io
  PyPI / Python-versions badge cache-bust suffix in lockstep with
  `__version__`.** The two badge URLs in `README.md` carry a
  `?cacheSeconds=120&v=X.Y.Z` query param — the `v=` portion is
  ignored by shields.io but is the cache key GitHub camo (the image
  proxy) uses, so bumping it forces readers' browsers to refetch the
  fresh PyPI version on release. Previously that bump was a manual
  README edit on the release branch (see PR #41, where v1.0.1 → 1.0.1
  cache-bust was hand-applied); now both `python scripts/bump_version.py
  X.Y.Z` and the orchestrator `scripts/release.py --apply` rewrite the
  badges idempotently. New `bump.update_readme_text` /
  `bump.write_readme_badges` helpers plus `--readme` CLI flag for
  test isolation; `scripts/release.py --dry-run` previews the badge
  diff alongside the `__init__.py` bump. Idempotent (silent no-op
  when README is missing or already at the target version). Closes
  follow-up "automate README badges + OpenSSF Scorecard" — PR A.

### Fixed

- **`_coerce_arrow_binary` (REST `tabledata.insertAll` BYTES path):
  strict base64 validation + clear errors for non-string inputs.**
  Previously the helper called `base64.b64decode(value)` without
  `validate=True`, which silently produced partial bytes on malformed
  input, AND fell through to `bytes(value)` for any non-string type
  (which would convert iterables of ints — masking real caller
  bugs). Now: strings go through `base64.b64decode(value, validate=True)`
  (matching BigQuery's `400 invalid: Could not decode bytes` on bad
  payloads); `bytes` / `bytearray` are passed through unchanged
  (preserves the Storage Write proto path in
  `streaming/proto_deserializer.py` that feeds proto-decoded BYTES
  fields here); any other type raises `TypeError` with a clear
  message. New unit tests cover all three branches plus the
  pre-existing happy path.
- **INTERVAL literal parsing: bad integer tokens now raise
  `ValidationError` instead of leaking `ValueError`.** The
  compound-parser path (`_consume_blocks` and helpers) used bare
  `int(...)` calls on user-supplied tokens; the wrapping
  `parse_interval_literal` documented `ValidationError` as the
  parse-failure exception but the bare conversion bypassed it on
  the `_consume_blocks` path. A new `_parse_int_token(raw, field=...)`
  helper centralises the `int(...) + try/except ValueError →
  raise ValidationError` pattern; all year / month / day / hour /
  minute call sites now route through it. New
  `test_invalid_inputs_raise` cases cover the year, month, day,
  hour, and minute parse-failure paths that previously leaked
  `ValueError`.

## [1.0.2] — 2026-05-23

### Fixed

- **scio example: end-to-end ``CustomersPipeline.run`` against
  bqemulator** (#17). The v1.0.1 wiring-only smoke flips to a real
  Beam BigQueryIO BATCH_LOADS round-trip — 3 rows written, 3 rows
  read back via ``jobs.query``. No emulator-side code change: the
  fix is contained in the scio example via (a) a new
  ``EmulatorBigQueryServices`` class in ``org.apache.beam.sdk.io.gcp.bigquery``
  that supplies the Apiary ``Bigquery`` client with
  ``setRootUrl(emulator)``, attached via
  ``BigQueryIO.Write.withTestServices(...)`` because Beam 2.55.1's
  Java SDK has no native ``BIGQUERY_EMULATOR_HOST`` support
  ([apache/beam#34037](https://github.com/apache/beam/pull/34037)
  is Go-side only); (b) a ``fsouza/fake-gcs-server`` testcontainers
  sidecar that handles Beam's GCS staging step for the BATCH_LOADS
  write method, bind-mounted into a shared directory with
  bqemulator's existing ``BQEMU_GCS_LOCAL_ROOT`` shim so the
  LOAD-job source URIs resolve to the same physical bytes Beam
  staged; (c) ``--gcpCredentialFactoryClass=NoopCredentialFactory``
  to short-circuit OAuth2 refresh against ``oauth2.googleapis.com``;
  (d) a ``testcontainers`` 1.20.4 → 1.21.4 bump because Docker 29+
  rejects docker-java clients announcing API < 1.40. See
  [ADR 0034](docs/adr/0034-scio-beam-emulator-routing.md) for the
  full design; the
  [scio example README](docs/examples/java/scio/README.md) has the
  user-facing recipe.

## [1.0.1] — 2026-05-23

### Fixed

- **Storage Read API IPC framing** (#15). The gRPC ``ReadRows`` handler
  previously packed a full Arrow IPC stream (schema-message + batches +
  EOS-marker) into ``ArrowRecordBatch.serialized_record_batch``, breaking
  every real Storage Read client — ``google-cloud-bigquery-storage``'s
  ``reader.to_arrow(session)`` tripped on ``OSError: Expected IPC message
  of type record batch but got schema``. The handler now emits only the
  record-batch IPC message bytes; the schema continues to travel
  separately via ``ReadSession.arrow_schema.serialized_schema`` and the
  first ``ReadRowsResponse.arrow_schema`` field, matching the BigQuery
  contract. ``serialize_arrow_ipc(table)`` in
  ``bqemulator.streaming.read_session`` is replaced by
  ``serialize_arrow_record_batch(batch)``; the pyspark-bigquery example
  drops its inline workaround and goes back to the natural
  ``reader.to_arrow(session)`` call. See ADR 0033 for the formal
  bare-message contract — dictionary-encoded columns at any nesting
  depth are rejected with ``ValueError`` at the producer boundary.

### Changed

- **scio example: testcontainers bump + #17 investigation notes.**
  Bumped ``testcontainers`` 1.19.7 → 1.20.4 in the scio example's
  ``build.sbt`` — the older docker-java 1.32 client doesn't talk to
  Docker 27+ (modern Docker Desktop returns ``client version 1.32 is
  too old``). The end-to-end Beam BigQueryIO routing attempted under
  issue #17 turned out to be deeper than a single flag/env-var fix
  — ``--bigQueryEndpoint`` does override the Apiary ``Bigquery``
  client's ``rootUrl``, but Beam's ``BigQueryIO.Write`` defaults to
  the ``BATCH_LOADS`` method which stages rows to GCS before
  invoking a BigQuery LOAD job (no GCS-compatible shim in the
  emulator), and Beam's auth refresh fires before the redirected
  HTTP call so ``OAuth2Credentials.refresh()`` 400s against
  ``oauth2.googleapis.com`` even with
  ``--gcpCredentialFactoryClass=NoopCredentialFactory``. The
  ``CustomersPipelineSpec`` stays at the wiring-only smoke for
  v1.0.1; the full set of constraints is captured in the spec's
  header comment and tracked on issue #17 for v1.0.2+.

## [1.0.0] — 2026-05-22

### Added

- **REST API parity** — Datasets, Tables, Jobs, TableData, Routines, Models,
  with multipart and resumable upload endpoints for `load_table_from_file`
  workflows. `INFORMATION_SCHEMA` views (`TABLES`, `COLUMNS`, `ROUTINES`,
  `VIEWS`, `MATERIALIZED_VIEWS`, `PARTITIONS`, `TABLE_OPTIONS`, etc.)
  queryable via the standard SQL path. The `JOBS` / `JOBS_BY_*` family is
  intentionally out of scope — see
  [`out-of-scope.md#information_schemajobs-family`](docs/reference/out-of-scope.md#information_schemajobs-family).
- **Storage Read API** — gRPC servicer with both Arrow and Avro wire formats.
  Avro is the Java client's default; both clients (Python `fastavro`, Node
  `avsc`, Go `linkedin/goavro`, Java canonical Apache Avro) interoperate
  against the same recorded fixtures.
- **Storage Write API** — gRPC servicer with all four stream types (`DEFAULT`,
  `COMMITTED`, `PENDING`, `BUFFERED`), both proto and Arrow row payload
  formats, `FinalizeWriteStream` / `BatchCommitWriteStreams` /
  `FlushRows` / `GetWriteStream`.
- **GoogleSQL translator** — SQLGlot-backed transpiler from BigQuery dialect
  to DuckDB SQL, with a rule registry covering the GoogleSQL function surface
  (date / time / timestamp / interval, string, array, struct, range,
  geography, statistical aggregates, approximate aggregates, JSON,
  regular expressions, bit ops, civil-time helpers, and more).
- **BigQuery scripting** — interpreter for `DECLARE` / `SET` / `BEGIN` …
  `END` / `IF` / `WHILE` / `FOR` / `LOOP` / `BREAK` / `CONTINUE` /
  `RETURN` / `RAISE` / `EXCEPTION WHEN ERROR THEN`, plus a
  `BEGIN TRANSACTION` / `COMMIT TRANSACTION` / `ROLLBACK TRANSACTION`
  shim.
- **User-defined functions** — SQL UDFs, table-valued functions (TVFs), and
  JavaScript UDFs via embedded V8 (optional `bqemulator[udf-js]` extra).
- **Versioning surface** — time travel (`FOR SYSTEM_TIME AS OF`), table
  snapshots, table clones, and materialized views with
  `BQ.REFRESH_MATERIALIZED_VIEW` dispatch.
- **Authorization surface** — authorized views (with RAP propagation) and
  row-access policies with caller-identity enforcement.
- **Specialized types** — `GEOGRAPHY` (planar via DuckDB-spatial with
  S2-sphere helpers for distance / length / area / perimeter / DWithin),
  `RANGE<DATE>` / `RANGE<DATETIME>` / `RANGE<TIMESTAMP>`, `INTERVAL`,
  `NUMERIC` / `BIGNUMERIC` arithmetic, civil-time helpers.
- **Load / extract formats** — load supports CSV, JSON, Avro, ORC, and
  Parquet. Extract supports CSV, JSON, Avro, and Parquet. (ORC extract is
  intentionally out of scope — see `docs/reference/out-of-scope.md`.)
- **Multi-arch Docker image** — `ghcr.io/jjviscomi/bqemulator` builds for
  `linux/amd64` + `linux/arm64`, with cosign keyless signatures via GitHub
  OIDC.
- **Native pytest plugin** — `pip install bqemulator` registers a pytest
  plugin; the `bqemu_server` fixture starts an ephemeral in-process emulator
  on random free ports, sets `BIGQUERY_EMULATOR_HOST`, and tears down on
  exit.
- **Five-client E2E** — every release exercises the live container against
  the official Python, Node.js, Go, and Java BigQuery client libraries plus
  Google's `bq` CLI.
- **Conformance corpus** — 1,200+ fixtures recorded against real BigQuery
  covering SQL semantics, REST wire format, and gRPC Storage R/W. Drift
  between the emulator and BigQuery surfaces as test failures; documented
  divergences are pinned in `tests/conformance/divergences.py` with ADR
  references.
- **Observability** — `structlog` JSON logs, OpenTelemetry tracing
  (configurable OTLP exporter), Prometheus metrics endpoint.
- **Admin surface** — `bqemulator import --from-project` clones a real
  BigQuery project's schema (and optionally data) into a local emulator
  data directory; backup / restore via `bqemulator backup` and
  `bqemulator restore`.
- **Release tooling** — `scripts/bump_version.py`, `scripts/changelog.py`,
  and `scripts/release.py` automate the version bump → changelog finalise
  → release commit + annotated tag flow. `make release-dry-run` previews;
  `make release` applies.
- **Example projects (14)** — `docs/examples/` ships runnable example
  projects for every supported language + framework + deployment pattern:
  Python (pytest-integration, dbt-local, airflow-dag-test,
  pyspark-bigquery), Node.js (NestJS app, Cloud Run local), Go (Beam
  pipeline, Dataflow-style ETL), Java/Scala (Spring Boot,
  Spotify Scio), docker-compose full-stack (app + emulator + Prometheus +
  Grafana), and CI recipes (GitHub Actions, GitLab CI, CircleCI). Each
  has its own `make test`; all are validated in CI by the new
  `.github/workflows/examples.yml` workflow (intentionally non-blocking
  on main so an upstream framework regression cannot stall emulator PRs).

### Documentation

- **mkdocs-Material site** — getting-started + per-language quickstarts +
  topic guides (loading data, querying, query parameters, streaming
  inserts, storage API, routines + UDFs, scripting, time travel,
  materialized views, row access policies, authorized views,
  `INFORMATION_SCHEMA`, GEOGRAPHY, RANGE, INTERVAL, admin endpoints, backup
  and restore, CI/CD patterns, dbt, Airflow, Spark, the `bq` CLI,
  observability).
- **Auto-generated reference docs** — compatibility matrix, conformance
  coverage matrix, SQL function mapping, and API coverage. Each ships
  with a `make <name>-check` drift gate wired into `make verify` and
  the per-PR `Docs-drift gates` CI job, so a regenerated doc can't
  drift from the live source between commits. A fifth audit doc —
  `docs/reference/api-configuration-coverage-matrix.md` — is the
  manually-maintained sibling that tracks the *configuration knob*
  surface (the part that can't be mechanically derived from the
  route handlers); it's labelled "Audit dated" at the top of the
  file and refreshed during the pre-release doc sweep.
- **Architecture Decision Records** — 32 ADRs documenting non-obvious
  design choices (DuckDB vs. alternatives, hexagonal architecture, scripting
  execution model, materialized-view refresh semantics, caller identity
  and row-access enforcement, conformance corpus design, divergence
  baseline, perf / chaos / mutation / fuzz / differential tier contracts,
  upload host endpoints, `bq` CLI as a fifth conformance client).

### Testing

- **7-tier test pyramid** — unit (hermetic), property (Hypothesis),
  integration (in-process + client), conformance (compared to real
  BigQuery), e2e (live container × five clients), performance
  (`pytest-benchmark` with `--benchmark-compare-fail=median:10%`), chaos
  (deliberately disruptive — concurrency, resource exhaustion, crash
  recovery, storage failures, network failures). Sibling tiers: differential
  (row-order perturbation of the conformance corpus), mutation (`mutmut`
  pilot on pure-domain modules), fuzz (Atheris on the SQL translator,
  dynamic-protobuf decoder, and Arrow bridge).
- **Coverage gate** — combined unit + property + integration coverage
  must reach 90% line + branch in CI.

### Security

- **Path-traversal hardening** on the resumable-upload `upload_id` regex
  (`^[A-Za-z0-9_-]{8,64}$`); size cap enforced before disk write to
  prevent unbounded resource consumption; multipart envelope injection
  prevented via stdlib `email.parser` plus a media-part type allowlist.
- **PyPI publish via Trusted Publishing** (no long-lived tokens); wheels
  carry sigstore attestations.
- **GHCR image signing** via keyless cosign with GitHub OIDC certificate
  identity.

### Known limitations (snapshot at v1.0.0 release; status as of v1.0.1)

The two caveats below were carried at v1.0.0 as documented
limitations to be addressed in v1.0.1. Status update after the
v1.0.1 release:

- ✅ **Storage Read API IPC bytes layout** — **CLOSED in v1.0.1**
  (see ``Fixed`` block under [Unreleased]/[1.0.1] above). The
  ``ReadRows`` handler now emits a bare record-batch IPC message
  per the BigQuery wire contract;
  ``google-cloud-bigquery-storage``'s
  ``reader.to_arrow(session)`` works unchanged; the
  ``python/pyspark-bigquery`` example dropped its inline
  workaround. See [ADR 0033](docs/adr/0033-storage-read-arrow-ipc-bare-message-contract.md)
  for the formal contract. ([#15](https://github.com/jjviscomi/bqemulator/issues/15))
- ✅ **Scio test exercises wiring only** — **CLOSED in v1.0.2**
  (see ``Fixed`` block under [Unreleased] above). The
  ``CustomersPipelineSpec`` now drives ``CustomersPipeline.run``
  end-to-end: 3 rows written via Beam BigQueryIO BATCH_LOADS, 3
  rows read back via ``jobs.query``. The v1.0.1 hypothesis that
  ``--bigQueryEndpoint`` worked turned out to be wrong (Beam
  Java SDK 2.55.1 has no such option — only the Go SDK has
  ``BIGQUERY_EMULATOR_HOST``); the actual fix uses
  ``BigQueryIO.Write.withTestServices(EmulatorBigQueryServices(
  endpoint))`` from a Beam-package-scoped helper in the scio
  example, plus a ``fake-gcs-server`` sidecar bind-mounted into
  bqemulator's existing ``BQEMU_GCS_LOCAL_ROOT`` shim for the
  BATCH_LOADS staging step. See
  [ADR 0034](docs/adr/0034-scio-beam-emulator-routing.md) for the
  decision record. ([#17](https://github.com/jjviscomi/bqemulator/issues/17))

