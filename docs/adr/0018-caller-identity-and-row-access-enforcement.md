# ADR 0018: Caller identity, row access policy enforcement, and authorized-view bypass

- **Status**: Accepted
- **Revision**: §"Authorized-view bypass" decision
  reversed. The P2.d follow-up recorded the 5
  ``authz_view_*`` conformance fixtures against real BigQuery in
  both same-dataset and cross-dataset topologies, and **all 5 returned
  0 rows in both** when the calling user was not a grantee of the
  base table's row-access policy. This proves empirically that real
  BigQuery enforces row-level security UNIVERSALLY through views
  (the published docs at
  ``cloud.google.com/bigquery/docs/row-level-security-intro#authorized_views_and_row-level_access_policies``
  confirm this — "Row-level access policies applied to the source
  table are still enforced" even via authorized views). The
  original decision to bypass RAP when a view was authorized on
  the base dataset (selected option #2 below) was a misreading of
  BQ semantics conflating IAM-level read access with row-level
  enforcement. The §"Authorized-view bypass" section is rewritten
  in place to reflect the corrected behaviour; the original text
  is preserved verbatim under
  §"Authorized-view bypass — superseded". Net code
  change: removed ~20 lines in
  [`src/bqemulator/sql/rewriter/row_access_filter.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/row_access_filter.py)
  (the ``is_authorized`` short-circuit) and unwired the
  ``current_view`` argument that was used only by that
  short-circuit. The
  [`AuthorizedViewManager`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/views/authorized_views.py)
  helper is retained — it's a pure-function-plus-cache utility
  that may be repurposed for future per-view IAM enforcement.
- **Implementation note**: closed the
  ``row_access/rap_filter_via_view`` XFAIL by reliably wiring
  the rewriter's existing ``_expand_view`` branch for *every*
  SQL-created view. Pre-fix, ``CREATE [OR REPLACE] VIEW`` DDL ran
  through DuckDB but no catalog record was written, so the rewriter's
  ``catalog.get_table(...)`` lookup returned ``None`` for SQL-created
  views and the view reference passed through unwrapped — DuckDB then
  expanded the view internally and read the base table with no RAP
  filtering. The fix lives in
  [`src/bqemulator/catalog/ddl_sync.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/catalog/ddl_sync.py):
  a new ``sync_created_view(bq_sql, project_id, ctx)`` helper parses
  ``CREATE VIEW`` SQL, introspects the freshly-created DuckDB view's
  schema, and upserts a ``TableMeta`` with ``table_type='VIEW'`` and
  ``view_query=<body>``. Both executor paths — the single-SQL path
  in [`src/bqemulator/jobs/executor.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py)
  and the scripting interpreter's ``_exec_sql`` in
  [`src/bqemulator/scripting/interpreter.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/scripting/interpreter.py)
  — now invoke the new helper alongside the existing
  ``sync_created_table`` call. This is **implementation, not
  contract**: the rules above stay valid — every view body still
  reaches the recursive ``_rewrite_table`` walk inside
  ``_expand_view`` and applies caller-bound policies on every
  base-table reference inside. The revision's "RAP applies
  through every view body regardless of authorization status" rule
  is preserved verbatim.

## Context

Phase 8 turns row access policies (RAP) from a stored-but-unenforced
artefact into an *enforced* one: a query against a protected table must
return only the rows the caller's matching policies allow. To do that
we need three pieces, none of which the emulator has had to answer
before:

1. **Who is the caller?** Real BigQuery answers that with IAM (an OAuth
   bearer token resolves to a Google identity). The emulator
   deliberately does not enforce IAM
   ([out-of-scope.md](../reference/out-of-scope.md#iam-enforcement)),
   yet the row-access rewriter still needs a caller identity to decide
   which policies match. Whatever we choose has to be deterministic
   for E2E tests in four client languages and round-trippable through
   both the REST surface and the gRPC surface.
2. **How do we combine multiple matching policies?** BigQuery
   documents row access policies as additive — a row is visible when
   *any* policy granting the caller matches it. We need to lock the
   combination semantics so the rewriter and the unit tests agree.
3. **Where does an authorized view bypass enforcement?** Authorized
   views are the BigQuery-native escape hatch — a view in an
   explicitly authorized dataset reads its base tables on behalf of
   the view, not the caller. We need to encode that bypass without
   over-applying it.

Three options were considered for the caller-identity carrier:

1. **`Authorization: Bearer <token>` parsing.** Reject. Real OAuth
   tokens are opaque; emulating a token store is far more surface area
   than Phase 8 needs. Tests would have to mint synthetic tokens, and
   gRPC clients route auth through metadata that the four client
   languages handle very differently.
2. **`X-Goog-User-Project: <project>`.** Reject as the *primary*
   identity carrier. The header exists in real BigQuery but its
   purpose is the *billing* project (so the operator knows whose quota
   to charge), not the caller's identity. Reusing it as identity would
   conflate two unrelated concepts and surprise anyone who ports their
   integration tests to real BigQuery. Acceptable as a fallback when
   no other identity is available.
3. **`X-Bqemu-Caller: <iam-member>` (selected).** A clearly
   emulator-scoped header carrying an IAM-member-shaped string
   (`user:alice@example.com`, `serviceAccount:sa@…`, `group:devs@…`,
   `domain:example.com`, `allAuthenticatedUsers`, `allUsers`). The
   prefix `X-Bqemu-` makes it impossible for the header to leak into
   real BigQuery traffic by accident, and the value uses BigQuery's
   own IAM-member grammar so policies can be written identically.

For the policy-combination question we considered:

1. **AND** — every matching policy's filter must hold. Reject: doesn't
   match BigQuery's documented "additive" semantics; surprises users
   migrating from real BigQuery.
2. **OR** (selected) — a row is visible when *any* matching policy's
   filter holds. Matches BigQuery and is the principle of least
   surprise.

For "no matching policy on a protected table":

1. **Pass through (return all rows)** — BigQuery's documented
   behaviour for *unprotected* tables.
2. **Empty (return zero rows)** (selected) — BigQuery's documented
   behaviour for *protected* tables when no policy matches the caller:
   the absence of a grant is denial. The rewriter injects
   `WHERE FALSE` (or its emitted equivalent).

For the authorized-view bypass:

1. **No bypass.** Reject — fails the ship criterion.
2. **Bypass only when the view's *containing dataset* is authorized
   on the *base table's dataset*** (selected). Matches BigQuery: the
   `access` array on the *base table's dataset* names the view as a
   reader; the rewriter looks up that array and skips the caller-
   bound filter when it finds a match. Per-view policies bound to the
   *view's own* protected base tables still apply normally.

## Decision

### Caller identity

The caller-identity resolver (`row_access/identity.py`) extracts an
IAM member string from each request in this order, returning the first
hit:

1. `X-Bqemu-Caller: <iam-member>` — primary, emulator-specific,
   value is the IAM member string used by the row-access matcher.
2. `X-Goog-User-Project: <project>` — fallback, mapped to the
   synthetic identity `user:caller@<project>.iam.gserviceaccount.com`.
   This lets clients that already pass the billing-project header
   (the standard BigQuery client libraries) trigger *different*
   per-project rewriting without mutating their request code, while
   making it clear the value is synthetic.
3. Default — `user:anonymous@bqemulator.local`. Tests written before
   Phase 8 (Phase 0–7) keep working because the default identity
   never matches any user-defined grantee.

The same resolver is used by the FastAPI and gRPC adapters. The gRPC
adapter reads the value from gRPC metadata using the same lower-cased
keys (`x-bqemu-caller` / `x-goog-user-project`) that the FastAPI
adapter reads from HTTP headers — gRPC normalises metadata keys to
lower case, so the two adapters share a single resolver.

The resolver returns a `CallerIdentity` value containing:
- `principal: str` — the IAM member string ("user:…", "group:…",
  "domain:…", "serviceAccount:…", "allUsers", "allAuthenticatedUsers",
  or "anonymous@bqemulator.local").
- `domains: tuple[str,...]` — the domain part of `principal` (if
  any), used by `domain:` matching.
- `is_authenticated: bool` — `False` only for the default fallback
  identity.

### Match rules

A grantee `G` matches caller `C` when **any** of:

- `G == "allUsers"`.
- `G == "allAuthenticatedUsers"` AND `C.is_authenticated`.
- `G` and `C.principal` are both `user:…` or both `serviceAccount:…`
  and the email parts are equal (case-insensitive on the domain part
  only — local part is case-sensitive per RFC 5321).
- `G == "domain:<d>"` AND `C` is a `user:` or `serviceAccount:` whose
  email host equals `<d>` (case-insensitive). Group/domain identities
  do NOT inherit domain matches.
- `G == "group:<email>"` AND the request supplied
  `X-Bqemu-Groups: <comma-separated-emails>` containing `<email>`.
  This is an emulator-only escape hatch — real BigQuery walks Google
  Workspace group membership, which the emulator obviously can't do.

Anything else is a non-match.

### Combination semantics

Inside a single table, the matching policies' `filter_predicate`s are
combined with **OR**. A row is visible when any matching policy's
filter holds.

When a query touches a table that **has** policies but **no** policy
matches the caller, the rewriter injects a guaranteed-false predicate.
That preserves the protected table's schema (the response still has
columns, just zero rows) and matches BigQuery's documented "absence
of a grant is denial" behaviour. Tables with **no** policies are left
alone.

### Rewriter shape

`sql/rewriter/row_access_filter.py` runs as a pre-translator pass on
the BigQuery SQL string (alongside the time-travel rewriter). It:

1. Short-circuits when the catalog has zero policies (the hot path
   for the overwhelming majority of queries).
2. Parses the SQL with SQLGlot in BigQuery dialect.
3. Walks every `exp.Table` reference. For each one:
 - Resolves the qualified `(project, dataset, table)` triple,
   defaulting `project` to the request's project_id and skipping
   references that lack a dataset (CTEs, function-call wrappers,
   etc.).
 - Looks up the table in the catalog. If the table is a `VIEW`,
   replaces the reference with a derived subquery built from the
   view's body, recursively rewriting that body with the bypass
   rules below.
 - For leaf tables (`TABLE`, `MATERIALIZED_VIEW`, `SNAPSHOT`,
   `CLONE`, or unknown / external), looks up the policies for
   that table and replaces the reference with
   `(SELECT * FROM <ref> WHERE <filter>) AS <alias>`. The alias
   preserves the user's original alias (or the table id if
   unaliased) so identifier resolution in downstream clauses keeps
   working.
4. Emits the rewritten BigQuery SQL.

The wrapping-as-derived-subquery shape is critical: it means the
caller's WHERE / SELECT / JOIN clauses see exactly the rows allowed
by the policy and can never re-read a row the policy filtered out.
Wrapping in a subquery (rather than appending an additional `AND`
to the user's WHERE) also preserves correctness for `OUTER JOIN`,
`UNION ALL`, and `WITH … AS` references, where pushing a predicate
into the wrong scope can change the result set.

### Authorized-view bypass — does not exist for RAP (revised)

Real BigQuery does **not** bypass row-level security for authorized
views. The `access_entries` array on a base dataset confers
*IAM-level read access* on the underlying data: a caller who queries
an authorized view does not need direct `bigquery.dataViewer` on the
base dataset because the access entry confers it transitively. But
row-level security — the per-row filter predicates declared by row
access policies — is enforced **per calling user**, against every
base-table read, regardless of whether the read is direct or through
a view. This was confirmed empirically by the 5 ``authz_view_*``
conformance fixtures (P2.d follow-up #1): both
same-dataset and cross-dataset authorized-view recordings returned 0
rows from real BQ when the calling user had no matching RAP grant,
matching the documented BQ behaviour at
[docs.cloud.google.com/bigquery/docs/row-level-security-intro](https://docs.cloud.google.com/bigquery/docs/row-level-security-intro)
("Row-level access policies applied to the source table are still
enforced" when the user queries through an authorized view).

The rewriter therefore applies caller-bound RAP enforcement to every
base-table reference inside a view body — same as if the caller had
queried the base table directly. Views that reference views are
expanded depth-first up to a recursion limit (8); the recursive
walk applies RAP at each level.

Per-view RAP entries (policies attached to the view *itself* as a
``TableMeta``) still apply when the view is referenced directly —
those go through the leaf-table path unchanged.

### Authorized-view bypass — superseded

Below is the original decision text, preserved for the
audit trail. The implementation it described was removed when this
ADR was revised.

> When the rewriter expands a `VIEW`, each base table inside the view's
> body is checked for authorization:
>
> - Read the *base table's* dataset's `access_entries` (the dataset
> doing the granting; matches BigQuery's "share-from" model).
> - If any entry is `{view: {projectId, datasetId, tableId}}` matching
> the *outer view*, skip caller-bound RAP application for that base
> table. Per-view RAP entries that target the view itself still
> apply.
>
> The bypass is checked at one level of view nesting. Views that
> reference views are expanded depth-first up to a sane recursion limit
> (8) — the emulator does not attempt to detect cyclic view definitions.
> A cyclic definition would already fail the SQL translator before
> reaching the rewriter.

### Information schema

`INFORMATION_SCHEMA.ROW_ACCESS_POLICIES` is implemented as an inline
`VALUES` rewrite, mirroring the
`INFORMATION_SCHEMA.MATERIALIZED_VIEWS` pattern from Phase 7. The
columns match the published BigQuery schema:

| Column | Type |
|------------------------|-------------|
| `table_catalog` | STRING |
| `table_schema` | STRING |
| `table_name` | STRING |
| `policy_name` | STRING |
| `grantees` | STRING |
| `filter_predicate` | STRING |
| `creation_time` | TIMESTAMP |
| `last_modified_time` | TIMESTAMP |

Grantees are emitted as a comma-separated string (the form BigQuery
emits when reading the column through SQL).

### CREATE / DROP ROW ACCESS POLICY via SQL DDL (added 2026-05-27)

Policies are managed both through the REST `rowAccessPolicies`
resource and through SQL DDL submitted to `jobs.query` / `jobs.insert`
(the `bq query` path). sqlglot's BigQuery grammar does not model the
RAP statements, so the executor detects and dispatches them with two
regexes (`_RAP_CREATE_RE` / `_RAP_DROP_RE`) ahead of the translator.
The accepted `CREATE` grammar matches BigQuery's:

```
CREATE [OR REPLACE] ROW ACCESS POLICY [IF NOT EXISTS] <policy>
  ON <table>
  [GRANT TO (<grantee_list>)]
  FILTER USING (<bool_expr>);
```

Decisions baked into the detector:

- **`IF NOT EXISTS` is accepted** (mirrors the `DROP … IF EXISTS`
  form). A form the detector does not match falls through to the
  translator and surfaces a parser error — it must never be a silent
  no-op.
- **`GRANT TO` is optional.** A policy created without it applies to
  every principal that can query the table; the emulator defaults the
  grantee list to `("allAuthenticatedUsers",)` — the closest analogue
  under the match rules above (an empty grantee tuple would match no
  one, the opposite of BigQuery's semantic).
- **Backtick-quoting** (whole-ref, per-component, and hyphenated
  project ids) is accepted for both the policy id and the table ref.
- **`classify_statement_type` recognises both forms** via the same
  regexes, so `statistics.query.statementType` reports
  `CREATE_ROW_ACCESS_POLICY` / `DROP_ROW_ACCESS_POLICY` (a DROP is no
  longer mislabelled as a CREATE).

The DDL target table must be catalog-visible — `RowAccessPolicyManager`
validates it via `catalog.get_table`. A table created purely through
SQL (`CREATE SCHEMA ds; CREATE TABLE ds.t …`) is now registered in the
catalog by the DDL-sync helpers (see ADR 0023 §1.F, amended the same
day), so SQL-only tables are valid RAP targets.

## Consequences

- **Positive.** A single short-circuit (`catalog.list_all_policies()`
  empty) keeps the hot path identical to Phase 7 for everyone who
  hasn't created a policy.
- **Positive.** The same resolver covers REST, gRPC Read, and gRPC
  Write because the metadata keys are identical between the two
  surfaces.
- **Positive.** The wrapping-subquery shape composes with the
  time-travel rewriter (which redirects table references *to a
  snapshot* before RAP runs) — a query against a snapshot still has
  RAP applied because the snapshot's protected-base-table policies
  carry over via the catalog lookup.
- **Negative.** Group membership cannot be modelled fully. The
  `X-Bqemu-Groups` escape hatch is a Phase-8-only compromise; it is
  not a real IAM substitute and is documented as such in the guide.
- **Negative.** A view whose body uses dynamic SQL (parameter
  substitution, `EXECUTE IMMEDIATE`) will not have its base tables
  detected and therefore cannot be authorized. Phase 8 documents
  this; a future RFC could add view-resolved scanning.
- **Negative.** The rewriter pays a one-time SQLGlot parse on every
  query that has *any* row access policy in the project. The
  short-circuit avoids it when there are no policies at all.

## Notes for future phases

- Phase 9+ adds `setIamPolicy` against the row-access-policy resource;
  the policy-combination rules above are the contract any setIamPolicy
  flow must respect.
- A future RFC could add `principalSet:` / workforce-pool support
  alongside `user:` / `group:` / `domain:` matching.
