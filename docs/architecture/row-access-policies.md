# Row access policies and authorized views

> Implementation lives in `src/bqemulator/row_access/`,
> `src/bqemulator/views/`, and
> `src/bqemulator/sql/rewriter/row_access_filter.py`. The full
> design record is [ADR 0018](../adr/0018-caller-identity-and-row-access-enforcement.md).
> User-facing guides:
> [row access policies](../guides/row-access-policies.md),
> [authorized views](../guides/authorized-views.md).

Unlike IAM (stored but not enforced), row access policies are
**enforced** — their filter predicate is injected into queries at
rewrite time. Authorized views are the documented BigQuery escape
hatch: a view in an authorized dataset reads its base tables on
behalf of the view, not the caller, so caller-bound RAP is bypassed
for that read.

## Pipeline

```
┌─────────────────┐     ┌────────────────┐     ┌─────────────────┐
│ REST adapter    │     │ scripting      │     │ gRPC Read API    │
│ (api/routes/    │     │ interpreter    │     │ servicer         │
│  jobs.py)       │     │                │     │                  │
└────────┬────────┘     └────────┬───────┘     └────────┬─────────┘
         │ resolve caller        │ resolve caller       │ resolve caller
         │ from headers          │ via parent client    │ from gRPC metadata
         ▼                       ▼                      ▼
┌────────────────────────────────────────────────────────────────────┐
│                rewrite_for_row_access (BigQuery → BigQuery)         │
│  • Walk every Table reference                                       │
│  • Skip DML write targets                                           │
│  • Expand VIEWs inline; check authorized-view bypass                │
│  • Wrap protected reads in (SELECT * FROM ref WHERE filter) alias   │
└────────────────────────────────────────────────────────────────────┘
         ▼
┌────────────────────────────────────────────────────────────────────┐
│  Time-travel rewriter → INFORMATION_SCHEMA expander → wildcard      │
│  expander → BigQuery → DuckDB translator → DuckDB                   │
└────────────────────────────────────────────────────────────────────┘
```

The rewriter runs *before* the time-travel rewriter so that a query
against a snapshot still has RAP applied (the snapshot's protected
base table inherits the policies via the catalog lookup).

## Caller-identity resolver

`bqemulator.row_access.identity` extracts an IAM-member identity
from each request:

| Source | Used for |
|--------------------------|-----------------------------------------------|
| `X-Bqemu-Caller` header | Primary identity carrier |
| `X-Goog-User-Project` | Synthetic fallback identity |
| Default | `user:anonymous@bqemulator.local` (no match) |
| `X-Bqemu-Groups` | Group membership escape hatch |

The resolver is shared between the FastAPI request layer and the
gRPC servicer (gRPC normalises metadata keys to lower case, so the
two adapters share a single resolver).

## Catalog model

`RowAccessPolicyMeta` (frozen Pydantic):

```python
RowAccessPolicyMeta(
    project_id="p", dataset_id="sales", table_id="orders",
    policy_id="eu_only",
    filter_predicate="region = 'EU'",
    grantees=("user:eu-analyst@example.com",),
    creation_time=..., last_modified_time=..., etag=...,
)
```

Stored in `_bqemulator_catalog.row_access_policies`; addressable
through the REST `/rowAccessPolicies` resource.

`DatasetMeta.access_entries` carries authorized-view entries:

```python
DatasetMeta(
    project_id="p", dataset_id="sales",
    access_entries=(
        AccessEntry(view=("p", "analytics", "all_orders")),
    ),
    ...,
)
```

Persisted in `_bqemulator_catalog.dataset_access_entries`.

## Matching rules

`bqemulator.row_access.matcher.grantee_matches` (per ADR 0018):

| Grantee shape | Matches when |
|-------------------------|-------------------------------------------------------|
| `allUsers` | Always |
| `allAuthenticatedUsers` | Caller is not the default anonymous fallback |
| `user:<email>` / `serviceAccount:<email>` | Email parts agree (host case-insensitive, local case-sensitive); kind matches caller principal kind |
| `domain:<host>` | Caller is a `user:` or `serviceAccount:` whose email host equals `<host>` (case-insensitive) |
| `group:<email>` | The request supplied `X-Bqemu-Groups` containing `<email>` |

Anything else is a non-match (the matcher is deliberately
fail-closed).

## Combination semantics

When a query touches a protected table:

- **Multiple matching policies** — filters OR-combined. A row is
  visible if any matching policy admits it.
- **No matching policy + table HAS policies** — the rewriter wraps
  with `WHERE FALSE`. Schema is preserved; no rows returned.
- **Table has NO policies** — the rewriter passes the reference
  through unchanged.

## Authorized-view bypass

When the rewriter walks into a `VIEW`'s body:

1. The outer view's identity is recorded as `current_view`.
2. For each base-table reference *inside* the view body, the
   rewriter consults the **base table's dataset's** `access_entries`.
3. If any entry's `view` field references the outer view, RAP is
   skipped for that base-table read.
4. Otherwise, RAP is applied to the base-table reference normally.

The bypass is checked at one level of nesting; views that read views
are walked depth-first up to a sane recursion limit.

## DML targets

Write targets (the destination of INSERT/UPDATE/DELETE/MERGE) are
**never** wrapped — RAP is read-only by design. The rewriter
collects every DML target's `id()` up front and skips those nodes
during the walk. This keeps the underlying SQL grammar valid and
matches BigQuery's documented semantics: RAP affects which rows the
caller can read, including in the WHERE clause of an UPDATE or
DELETE.

## INFORMATION_SCHEMA.ROW_ACCESS_POLICIES

Implemented as an inline `VALUES` rewrite, mirroring the
`MATERIALIZED_VIEWS` pattern. Columns:

| Column | Type |
|------------------------|-------------|
| `table_catalog` | STRING |
| `table_schema` | STRING |
| `table_name` | STRING |
| `policy_name` | STRING |
| `grantees` | STRING (comma-separated) |
| `filter_predicate` | STRING |
| `creation_time` | TIMESTAMP |
| `last_modified_time` | TIMESTAMP |

Project-, dataset-, and bare-qualified forms are supported; bare
returns an empty result set (matches BigQuery's scoping rule).

## Performance characteristics

- One SQLGlot parse per query (already paid by the time-travel
  rewriter).
- One catalog lookup per Table reference, cached within a single
  rewrite pass.
- One `list_all_row_access_policies` lookup at the gRPC Read API
  short-circuit; skipped entirely when no policies exist.
- The rewriter is deterministic — repeated rewrites of the same
  input produce the same output.

## Out of scope

- IAM enforcement (see
  [out-of-scope.md](../reference/out-of-scope.md#iam-enforcement)).
- Real Google-Workspace group membership lookup.
- `principalSet:` / workforce-pool / workload-identity-pool
  matching.
- Authorized routines and authorized datasets (the access-entry
  shapes are accepted on dataset round-trip but have no enforcement
  effect in v1).

## Related ADRs

- [ADR 0018 — Caller identity and row-access enforcement](../adr/0018-caller-identity-and-row-access-enforcement.md)
