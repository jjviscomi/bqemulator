# API coverage

A flat inventory of every wire-facing endpoint and gRPC RPC the
emulator exposes. For ship-status (does this surface match real
BigQuery?) see the [compatibility matrix](compatibility-matrix.md).
For per-rule SQL translation behaviour see the
[SQL function mapping](sql-function-mapping.md).

The inventory tables below are **auto-generated** by
[`scripts/generate_api_coverage.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/generate_api_coverage.py)
from the live route handlers and gRPC servicers. `make verify`
runs the script's `--check` mode to refuse merging a PR whose
committed inventory has drifted from the source.

<!-- BEGIN AUTO-GENERATED API INVENTORY -->

## REST endpoints

> **Auto-generated.** Edit route handlers under [`src/bqemulator/api/routes/`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/api/routes/) (or the root-level health router under [`src/bqemulator/api/health.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/api/health.py)) and run `make api-coverage` to regenerate this block. `make verify` calls `--check` to refuse merging a PR whose committed inventory has drifted from the live source. Endpoint counts in this block are facts about the codebase; ship-status (v1.0.0 release-quality across all surfaces) is asserted in the [compatibility matrix](compatibility-matrix.md) and gated by the conformance corpus on every PR.

- **Total REST endpoints**: 46 across 10 route modules

| Group | Path | Methods |
|---|---|---|
| Health & metrics | `/healthz` | GET |
| Health & metrics | `/readyz` | GET |
| Projects | `/bigquery/v2/projects` | GET |
| Projects | `/bigquery/v2/projects/{project_id}/serviceAccount` | GET |
| Datasets | `/bigquery/v2/projects/{project_id}/datasets` | GET POST |
| Datasets | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}` | GET PUT PATCH DELETE |
| Tables | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables` | GET POST |
| Tables | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}` | GET PUT PATCH DELETE |
| TableData | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/data` | GET |
| TableData | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/insertAll` | POST |
| Jobs | `/bigquery/v2/projects/{project_id}/jobs` | GET POST |
| Jobs | `/bigquery/v2/projects/{project_id}/queries` | POST |
| Jobs | `/bigquery/v2/projects/{project_id}/jobs/{job_id}` | GET DELETE |
| Jobs | `/bigquery/v2/projects/{project_id}/queries/{job_id}` | GET |
| Jobs | `/bigquery/v2/projects/{project_id}/jobs/{job_id}/cancel` | POST |
| Jobs | `/bigquery/v2/projects/{project_id}/jobs/{job_id}/delete` | DELETE |
| Routines | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/routines` | GET POST |
| Routines | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/routines/{routine_id}` | GET PUT PATCH DELETE |
| Models | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/models` | GET |
| Models | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/models/{model_id}` | GET PATCH DELETE |
| RowAccessPolicies | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies` | GET POST |
| RowAccessPolicies | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies:batchDelete` | POST |
| RowAccessPolicies | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies/{policy_id}` | GET PUT DELETE |
| RowAccessPolicies | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies/{policy_id}:getIamPolicy` | POST |
| RowAccessPolicies | `/bigquery/v2/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies/{policy_id}:testIamPermissions` | POST |
| Upload host | `/upload/bigquery/v2/projects/{project_id}/jobs` | POST PUT |

## gRPC services

- **Total gRPC RPCs**: 9 across 2 services

| Service | RPC method |
|---|---|
| `google.cloud.bigquery.storage.v1.BigQueryRead` | `CreateReadSession` |
| `google.cloud.bigquery.storage.v1.BigQueryRead` | `ReadRows` |
| `google.cloud.bigquery.storage.v1.BigQueryRead` | `SplitReadStream` |
| `google.cloud.bigquery.storage.v1.BigQueryWrite` | `AppendRows` |
| `google.cloud.bigquery.storage.v1.BigQueryWrite` | `BatchCommitWriteStreams` |
| `google.cloud.bigquery.storage.v1.BigQueryWrite` | `CreateWriteStream` |
| `google.cloud.bigquery.storage.v1.BigQueryWrite` | `FinalizeWriteStream` |
| `google.cloud.bigquery.storage.v1.BigQueryWrite` | `FlushRows` |
| `google.cloud.bigquery.storage.v1.BigQueryWrite` | `GetWriteStream` |

<!-- END AUTO-GENERATED API INVENTORY -->
