# Row access policies

> Row access policies are **stored AND enforced** in bqemulator
> (unlike IAM, which is stored but not enforced — see
> [out-of-scope.md](../reference/out-of-scope.md#iam-enforcement)).

A row access policy restricts which rows of a table a particular
caller can read. The policy carries a SQL boolean filter and a list
of IAM members (the grantees). When a granted caller queries the
table, the emulator rewrites the query so it only sees rows matching
the filter. When a non-granted caller queries the table, the
emulator returns zero rows (BigQuery's "absence is denial" rule).

The full design is captured in
[ADR 0018](../adr/0018-caller-identity-and-row-access-enforcement.md).

## Caller identity

The emulator deliberately does not enforce IAM — see
[out-of-scope.md](../reference/out-of-scope.md#iam-enforcement) for
why. Row-access enforcement still needs to know *who* the caller is,
so each request supplies the caller via a header:

| Header | Purpose |
|-------------------------|--------------------------------------------------|
| `X-Bqemu-Caller` | The caller's IAM member string. Primary. |
| `X-Goog-User-Project` | Standard BigQuery billing-project header. Falls back to a synthetic identity if `X-Bqemu-Caller` is absent. |
| `X-Bqemu-Groups` | Comma-separated group emails the caller belongs to. Used for `group:` grantee matching. Optional. |

If neither header is present, the caller defaults to
`user:anonymous@bqemulator.local`, which never matches a
user-defined grantee. Queries from the default caller against a
protected table return zero rows.

`X-Bqemu-Caller` accepts BigQuery's IAM-member grammar: `user:…`,
`serviceAccount:…`, `group:…`, `domain:…`, `allUsers`, or
`allAuthenticatedUsers`.

## Creating a policy

A policy can be created two ways. Both persist to the same catalog
and are enforced identically.

### SQL DDL (`bq query`)

BigQuery's GoogleSQL `CREATE ROW ACCESS POLICY` statement, submitted
through `jobs.query` or `jobs.insert`:

```sql
CREATE ROW ACCESS POLICY eu_only
  ON sales.orders
  GRANT TO ('user:eu-analyst@example.com')
  FILTER USING (region = 'EU');
```

`CREATE OR REPLACE` and `IF NOT EXISTS` are accepted. Omitting the
`GRANT TO` clause applies the policy to every authenticated caller
(BigQuery's grantee-less semantic). Drop a policy with
`DROP ROW ACCESS POLICY [IF EXISTS] eu_only ON sales.orders`. The
target table must exist — a table created entirely through SQL
(`CREATE SCHEMA sales; CREATE TABLE sales.orders …`) is registered in
the catalog automatically, so it is a valid policy target.

### REST resource

The `/rowAccessPolicies` resource mirrors the SDK shape:

```bash
curl -X POST \
  http://localhost:9050/bigquery/v2/projects/demo/datasets/sales/tables/orders/rowAccessPolicies \
  -H 'Content-Type: application/json' \
  -d '{
    "rowAccessPolicyReference": {
      "projectId": "demo",
      "datasetId": "sales",
      "tableId": "orders",
      "policyId": "eu_only"
    },
    "filterPredicate": "region = '\''EU'\''",
    "grantees": ["user:eu-analyst@example.com"]
  }'
```

The Python `google-cloud-bigquery` client also exposes the resource
via `client._http` raw calls; future versions of the client are
expected to add a higher-level wrapper.

## Querying as a granted caller

```python
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery

session = AuthorizedSession(AnonymousCredentials())
session.headers["X-Bqemu-Caller"] = "user:eu-analyst@example.com"

client = bigquery.Client(
    project="demo",
    credentials=AnonymousCredentials(),
    client_options=ClientOptions(api_endpoint="http://localhost:9050"),
    _http=session,
)

rows = list(client.query("SELECT id FROM `demo.sales.orders` ORDER BY id"))
# Only EU rows visible: [Row((1,)), Row((2,))]
```

## Querying as a non-granted caller

The same query with a different caller header returns zero rows:

```python
session.headers["X-Bqemu-Caller"] = "user:other@example.com"
rows = list(client.query("SELECT id FROM `demo.sales.orders`"))
# []
```

## Multiple policies on the same table

When more than one policy grants the caller, the emulator OR-combines
the filters — a row is visible if **any** matching policy admits it.
This matches BigQuery's documented "additive" semantics.

```bash
# Policy 1: EU rows only
curl -X POST .../rowAccessPolicies -d '{"rowAccessPolicyReference":...,
  "filterPredicate":"region = '\''EU'\''", "grantees":["user:m@x"]}'

# Policy 2: VIP rows only
curl -X POST .../rowAccessPolicies -d '{"rowAccessPolicyReference":...,
  "filterPredicate":"vip = TRUE", "grantees":["user:m@x"]}'
```

The granted caller now sees rows where `region = 'EU' OR vip = TRUE`.

## Group-based grantees

Real BigQuery walks Google Workspace group membership; the emulator
can't. To exercise `group:` grantees in tests, supply
`X-Bqemu-Groups` alongside `X-Bqemu-Caller`:

```python
session.headers["X-Bqemu-Caller"] = "user:eu-analyst@example.com"
session.headers["X-Bqemu-Groups"] = "data-readers@example.com"
```

A grantee `group:data-readers@example.com` then matches.

## Listing and managing policies

```bash
# List policies on a table
curl http://localhost:9050/bigquery/v2/projects/demo/datasets/sales/tables/orders/rowAccessPolicies

# Update (PUT replaces the policy entirely)
curl -X PUT .../rowAccessPolicies/eu_only -d '...'

# Delete
curl -X DELETE .../rowAccessPolicies/eu_only

# Batch-delete several at once
curl -X POST .../rowAccessPolicies:batchDelete \
  -d '{"policyIds":["eu_only", "vip"]}'

# IAM-shaped read of grantees
curl -X POST .../rowAccessPolicies/eu_only:getIamPolicy
```

## INFORMATION_SCHEMA.ROW_ACCESS_POLICIES

```sql
SELECT policy_name, table_name, grantees, filter_predicate
FROM `demo.sales.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`;
```

| Column | Type |
|-----------------------|-----------|
| `table_catalog` | STRING |
| `table_schema` | STRING |
| `table_name` | STRING |
| `policy_name` | STRING |
| `grantees` | STRING (comma-separated) |
| `filter_predicate` | STRING |
| `creation_time` | TIMESTAMP |
| `last_modified_time` | TIMESTAMP |

## Limitations

- Row access policies cannot be attached to `SNAPSHOT` or
  `MATERIALIZED_VIEW` tables (those are read-only artefacts).
- Filter predicates may not contain subqueries or DML keywords. The
  emulator pre-screens against `SELECT`/`FROM`/`INSERT`/`UPDATE`/
  `DELETE`/`MERGE` keywords and rejects unparseable expressions.
- DML write *targets* are never wrapped — RAP is read-only by design.
  An `UPDATE` against a protected table mutates the rows the caller
  matched (the emulator does not silently widen the WHERE clause).
- Group membership is modelled via the `X-Bqemu-Groups` header; a
  real Google-Workspace group lookup is out of scope.

## See also

- [Authorized views](authorized-views.md) — bypass row access on
  base tables for a specific view.
- [ADR 0018](../adr/0018-caller-identity-and-row-access-enforcement.md)
  — the full design record.
- [Architecture: row access policies](../architecture/row-access-policies.md).
