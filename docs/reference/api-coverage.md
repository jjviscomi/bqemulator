# API coverage

Auto-generated from `src/bqemulator/api/routes/*.py` and
`src/bqemulator/grpc_api/*_servicer.py`. See the
[compatibility matrix](compatibility-matrix.md) for ship-phase status
per operation.

## REST endpoints registered

| Route | Methods | Phase |
|---|---|---|
| `/healthz` | GET | 0 ✅ |
| `/readyz` | GET | 0 ✅ |
| `/metrics` | GET | 0 ✅ |
| `/bigquery/v2/projects` | GET | 1 🚧 |
| `/bigquery/v2/projects/{p}/datasets` | GET POST | 1 🚧 |
| `/bigquery/v2/projects/{p}/datasets/{d}` | GET PATCH PUT DELETE | 1 🚧 |
| `/bigquery/v2/projects/{p}/datasets/{d}/tables` | GET POST | 1 🚧 |
| `/bigquery/v2/projects/{p}/datasets/{d}/tables/{t}` | GET PATCH PUT DELETE | 1 🚧 |
| `/bigquery/v2/projects/{p}/datasets/{d}/tables/{t}/insertAll` | POST | 2 🚧 |
| `/bigquery/v2/projects/{p}/datasets/{d}/tables/{t}/data` | GET | 2 🚧 |
| `/bigquery/v2/projects/{p}/queries` | POST | 1 🚧 |
| `/bigquery/v2/projects/{p}/jobs` | GET POST | 1/2 🚧 |
| `/bigquery/v2/projects/{p}/jobs/{j}` | GET POST DELETE | 2 🚧 |
| `/bigquery/v2/projects/{p}/jobs/{j}/cancel` | POST | 2 🚧 |
| `/bigquery/v2/projects/{p}/queries/{j}` | GET | 2 🚧 |
| `/bigquery/v2/projects/{p}/datasets/{d}/routines` | GET POST | 6 🚧 |
| `/bigquery/v2/projects/{p}/datasets/{d}/routines/{r}` | GET PATCH PUT DELETE | 6 🚧 |
| `/upload/bigquery/v2/projects/{p}/jobs?uploadType=media` | POST | 11 (G2) ✅ |
| `/upload/bigquery/v2/projects/{p}/jobs?uploadType=multipart` | POST | 11 (G2) ✅ |
| `/upload/bigquery/v2/projects/{p}/jobs?uploadType=resumable` | POST | 11 (G2) ✅ |
| `/upload/bigquery/v2/projects/{p}/jobs?upload_id={id}` | PUT | 11 (G2) ✅ |

## gRPC services registered

| Service | Methods | Phase |
|---|---|---|
| `grpc.health.v1.Health` | `Check`, `Watch` | 0 ✅ |
| `google.cloud.bigquery.storage.v1.BigQueryRead` | `CreateReadSession`, `ReadRows` (Arrow + **Avro** as of G3 2024-05-21), `SplitReadStream` | 4 + 11 (G3) ✅ |
| `google.cloud.bigquery.storage.v1.BigQueryWrite` | `CreateWriteStream`, `AppendRows`, `FinalizeWriteStream`, `BatchCommitWriteStreams`, `FlushRows`, `GetWriteStream` | 5 🚧 |
