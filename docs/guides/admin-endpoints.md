# Admin endpoints

The emulator exposes a small **read-only** diagnostic surface under
`/admin/*` that returns JSON describing the running process's catalog,
jobs, streams, and effective configuration. The endpoints are
**opt-in** — they exist only when `Settings.admin_enabled` is True.

## Enabling

Pass `--enable-admin` on the CLI, or set `BQEMU_ADMIN_ENABLED=1` in the
environment:

```bash
bqemulator start --ephemeral --enable-admin
# or
BQEMU_ADMIN_ENABLED=1 bqemulator start --ephemeral
```

The testcontainer wrapper
(`bqemulator.testing.testcontainers.BigQueryEmulatorContainer`) sets the
env var automatically so E2E tests can use the admin surface without a
custom image build.

## Threat model

The admin endpoints are **diagnostic-only and unauthenticated**.

- They never reveal row data.
- They never mutate state.
- They respect the `Settings._REDACTED_FIELDS` allow-list — fields with
  known-secret semantics are blanked in `/admin/config` responses.

Do not enable in any environment that exposes the emulator's port to
untrusted callers. ADR 0020 records the full reasoning.

## Endpoints

### `GET /admin/jobs`

Returns every job the catalog tracks. Optional filters:

| Query | Type | Default | Description |
|---|---|---|---|
| `projectId` | string | (all projects) | Filter to a single project |
| `state` | string | (any) | Filter to `PENDING` / `RUNNING` / `DONE` |
| `maxResults` | int | 1000 | Cap on results (≤ 10000) |

Response shape:

```json
{
  "kind": "bqemu#adminJobList",
  "totalItems": 2,
  "jobs": [
    {
      "projectId": "p",
      "jobId": "job-1",
      "jobType": "QUERY",
      "state": "DONE",
      "creationTime": "2024-05-14T00:00:00+00:00",
      "startTime": "2024-05-14T00:00:00+00:00",
      "endTime": "2024-05-14T00:00:01+00:00",
      "userEmail": null,
      "errorResult": null,
      "statistics": {}
    }
  ]
}
```

### `GET /admin/catalog`

Returns the dataset / table / routine catalog grouped by project.
Optional `projectId` filter.

```json
{
  "kind": "bqemu#adminCatalog",
  "totalProjects": 1,
  "totalDatasets": 1,
  "projects": [
    {
      "projectId": "p",
      "datasets": [
        {
          "datasetId": "d",
          "location": "US",
          "labels": {"team": "data"},
          "tables": [
            {
              "tableId": "orders",
              "tableType": "TABLE",
              "numRows": 0,
              "numBytes": 0,
              "schemaFields": ["id", "amount"],
              "partitioned": true,
              "clustered": false
            }
          ],
          "routines": [
            {"routineId": "inc", "routineType": "SCALAR_FUNCTION", "language": "SQL"}
          ]
        }
      ]
    }
  ]
}
```

### `GET /admin/streams`

Returns active Storage Read / Write streams.

```json
{
  "kind": "bqemu#adminStreamList",
  "writeStreams": [
    {
      "name": "projects/p/datasets/d/tables/t/streams/abc",
      "projectId": "p",
      "datasetId": "d",
      "tableId": "t",
      "streamType": "COMMITTED",
      "state": "OPEN",
      "nextOffset": 0,
      "rowCount": 0,
      "bufferedBatches": 0,
      "flushedRows": 0
    }
  ],
  "writeStreamCount": 1,
  "readSessions": [],
  "readSessionCount": 0
}
```

### `GET /admin/config`

Returns the effective `Settings`. Fields in the (currently empty)
redaction allow-list render as `"[REDACTED]"`.

```json
{
  "kind": "bqemu#adminConfig",
  "settings": {
    "rest_host": "127.0.0.1",
    "rest_port": 9050,
    "admin_enabled": true,
    "persistence_mode": "ephemeral",
    "...": "..."
  }
}
```

## Calling from a client

The endpoints are vanilla REST. From Python:

```python
import httpx
print(httpx.get("http://localhost:9050/admin/catalog").json())
```

From `curl`:

```bash
curl -s http://localhost:9050/admin/jobs | jq
```

## When admin is off

When `admin_enabled` is False (the default), every `/admin/*` path
returns `404 Not Found`. Clients can detect this and either skip
diagnostics or fall back to the metrics endpoint at `/metrics`.
