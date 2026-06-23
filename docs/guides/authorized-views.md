# Authorized views

> An authorized view is a regular VIEW that has been granted read
> access on its base tables, so callers can query the view *without*
> needing their own IAM read on the underlying data.
>
> **Authorized views do NOT bypass row-level security.** Row-access
> policies (RAP) on the base tables are evaluated against the calling
> user for every read, including reads that route through an
> authorized view. This matches real BigQuery: see
> [cloud.google.com/bigquery/docs/row-level-security-intro](https://docs.cloud.google.com/bigquery/docs/row-level-security-intro)
> ("Row-level access policies applied to the source table are still
> enforced" when querying through an authorized view). ADR 0018 was
> originally drafted with the opposite assumption and was revised on
> once the 5 ``authz_view_*`` conformance fixtures
> empirically confirmed the documented behaviour.

The contract: the *base table's dataset* lists the view (or the
view's dataset) in its `access` array. That entry confers
**IAM-level** read access: the calling user does not need direct
`bigquery.dataViewer` on the base dataset; the view reads it on the
user's behalf. But **caller-bound RAP filters still apply** to every
base-table reference inside the view body, so a user who is not a
grantee of the base table's row access policies will still see only
the rows their grants permit (or zero rows if no policy matches).

The full design is captured in
[ADR 0018](../adr/0018-caller-identity-and-row-access-enforcement.md)
(revised).

## Setup walk-through

The emulator stores `access_entries` on the dataset metadata. To
authorize a view:

1. Create the protected base table and apply a row access policy
   to it.
2. Create the view in a different dataset.
3. Update the **base table's** dataset to add an access entry whose
   `view` field references the view.

```python
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import httpx

PROJECT = "demo"
PROTECTED_DS = "sales"
VIEW_DS = "analytics"
EMULATOR = "http://localhost:9050"

client = bigquery.Client(
    project=PROJECT,
    credentials=AnonymousCredentials(),
    client_options=ClientOptions(api_endpoint=EMULATOR),
)

# 1. Create the protected base table and a row access policy.
client.create_dataset(bigquery.Dataset(f"{PROJECT}.{PROTECTED_DS}"), exists_ok=True)
client.create_dataset(bigquery.Dataset(f"{PROJECT}.{VIEW_DS}"), exists_ok=True)
client.create_table(
    bigquery.Table(
        f"{PROJECT}.{PROTECTED_DS}.orders",
        schema=[
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField("region", "STRING"),
        ],
    ),
)
client.query(
    f"INSERT INTO `{PROJECT}.{PROTECTED_DS}.orders` VALUES (1, 'EU'), (2, 'EU'), (3, 'US')"
).result()

# 2. Create the view in a separate dataset.
client.create_table(
    bigquery.Table(
        f"{PROJECT}.{VIEW_DS}.all_orders",
        view_query=f"SELECT id, region FROM `{PROJECT}.{PROTECTED_DS}.orders`",
    ),
)

# 3. Add an access entry on the BASE table's dataset granting the view.
with httpx.Client(base_url=EMULATOR) as h:
    h.patch(
        f"/bigquery/v2/projects/{PROJECT}/datasets/{PROTECTED_DS}",
        json={
            "access": [
                {"view": {"projectId": PROJECT, "datasetId": VIEW_DS, "tableId": "all_orders"}},
            ],
        },
    )
    # 4. Add a row access policy that hides every row from anyone except
    #    the documented EU analyst (grants are caller-bound).
    h.post(
        f"/bigquery/v2/projects/{PROJECT}/datasets/{PROTECTED_DS}/tables/orders/rowAccessPolicies",
        json={
            "rowAccessPolicyReference": {
                "projectId": PROJECT,
                "datasetId": PROTECTED_DS,
                "tableId": "orders",
                "policyId": "eu_only",
            },
            "filterPredicate": "region = 'EU'",
            "grantees": ["user:eu-analyst@example.com"],
        },
    )
```

## Reading via the view: IAM bypass, RAP still applies

```python
from google.auth.transport.requests import AuthorizedSession

session = AuthorizedSession(AnonymousCredentials())
session.headers["X-Bqemu-Caller"] = "user:other@example.com"
client = bigquery.Client(
    project=PROJECT,
    credentials=AnonymousCredentials(),
    client_options=ClientOptions(api_endpoint=EMULATOR),
    _http=session,
)

# Direct read by `user:other@example.com`: caller is not a grantee
# of the eu_only policy, so the RAP filter zero-rows the result.
direct = list(
    client.query(f"SELECT id FROM `{PROJECT}.{PROTECTED_DS}.orders`")
)
assert direct == []

# Via the authorized view: the IAM check on the base dataset is
# bypassed (no `bigquery.dataViewer` needed for the caller), but the
# row-access policy on `orders` is STILL evaluated against the
# caller. `user:other@example.com` is not a grantee → zero rows.
via_view = list(
    client.query(f"SELECT id FROM `{PROJECT}.{VIEW_DS}.all_orders`")
)
assert via_view == []

# As `user:eu-analyst@example.com`, the policy matches and the
# 2 EU rows are returned.
session.headers["X-Bqemu-Caller"] = "user:eu-analyst@example.com"
eu_view = list(
    client.query(f"SELECT id FROM `{PROJECT}.{VIEW_DS}.all_orders` ORDER BY id")
)
assert [r.id for r in eu_view] == [1, 2]
```

## Removing authorization

To revoke, replace the dataset's `access` array with one that omits
the view entry:

```python
with httpx.Client(base_url=EMULATOR) as h:
    h.patch(
        f"/bigquery/v2/projects/{PROJECT}/datasets/{PROTECTED_DS}",
        json={"access": []},
    )
```

After the patch, the view loses its IAM-level read on the base
dataset. The caller would need their own `bigquery.dataViewer` on
`sales` to query through `analytics.all_orders`. (The emulator does
not enforce IAM — see [out-of-scope.md](../reference/out-of-scope.md#iam-enforcement)
— so on the emulator the practical effect of authorization
add/remove is observable through the access-entries metadata only.
Row-access policy enforcement is unchanged in either case.)

## Limitations

- Views whose body uses `EXECUTE IMMEDIATE` or other dynamic SQL
  cannot have their base tables enumerated; the rewriter cannot
  apply RAP statically to those reads. Documented in
  [out-of-scope.md](../reference/out-of-scope.md#iam-enforcement)
  alongside the IAM caveat.
- Authorized *routines* (UDFs/TVFs) are accepted in the
  `access` array shape but have no enforcement effect in v1.
- IAM enforcement itself is out of scope for v1.0 — see
  [out-of-scope.md#iam-enforcement](../reference/out-of-scope.md#iam-enforcement).
  In practice this means the emulator returns rows even when real
  BigQuery would reject the caller; the row-level filter (which IS
  implemented) is the boundary the emulator does enforce.

## See also

- [Row access policies](row-access-policies.md) — the per-row
  filters that authorized views do **not** bypass.
- [ADR 0018](../adr/0018-caller-identity-and-row-access-enforcement.md)
  — Caller identity, row-access enforcement, and the revised
  authorized-view contract.
