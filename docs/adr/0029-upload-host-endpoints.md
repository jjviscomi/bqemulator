# ADR 0029: Upload-host endpoints (G2 — multipart + resumable)

- **Status**: Accepted

## Context

The v1.0 competitor-parity workstream G2 closes the single row of
the [goccy `bigquery-emulator` feature
matrix](https://github.com/goccy/bigquery-emulator/blob/main/docs/feature-support.md)
where bqemulator silently lacked the canonical local-file load path:

| Gap | Before | After |
|---|---|---|
| **G-4** Load from a local file (multipart / resumable upload) | ❌ no `/upload/bigquery/v2/...` routes existed | ✅ |

The four official BigQuery client libraries (Python, Node, Go, Java)
all route `client.load_table_from_file(io.BytesIO(...))` style calls
through the upload host — a separate URL prefix
(`https://bigquery.googleapis.com/upload/bigquery/v2/...`) distinct
from the data-plane prefix
(`https://bigquery.googleapis.com/bigquery/v2/...`). Without those
routes, any caller using `load_table_from_file` got a `404 Not Found`
back from the emulator's FastAPI app and had to manually convert the
load into a `gs://` URI flow (Phase 3) or `tabledata.insertAll` flow
(Phase 2) — neither of which exercises the upload-protocol code path
that real production code uses.

The constraints to satisfy:

1. **Wire-format parity** — three documented upload protocols
   (media / multipart / resumable) with the exact response shapes
   BigQuery emits: `Location` + `X-GUploader-UploadID` on resumable
   initiation; `308 Resume Incomplete` + `Range: bytes=0-N` on
   partial chunks; final `200` with the `Job` resource on the last
   chunk.
2. **Security** — the new REST surface accepts arbitrary file bytes;
   that's exactly the threat surface AGENTS.md's security-review
   non-negotiable was written for. Specifically, path traversal,
   size-cap overrun, multipart envelope injection, and
   `Content-Length` spoofing.
3. **Coverage** — every new branch hits the ≥90% line+branch gate.
4. **Conformance shape** — 12 HTTP corpus fixtures recorded against
   real BigQuery (or hand-authored against the emulator with a TODO
   to re-record when operator credentials are available). The
   recorder framework is extended to support binary request bodies
   via a sibling `request.body.bin` file.
5. **Four-language E2E** — Python / Node / Go / Java suites each get
   two upload-protocol tests against a fresh container.
6. **Cleanup** — temp files must not leak even when the executor
   raises mid-load.

## Decisions

### 1. New router at `/upload/bigquery/v2`

[`src/bqemulator/api/routes/upload.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/api/routes/upload.py)
hosts the four endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/projects/{p}/jobs?uploadType=media` | Reserved — rejected with 400 (mirrors real BQ; load jobs accept only multipart and resumable). |
| `POST` | `/projects/{p}/jobs?uploadType=multipart` | Single-shot multipart/related upload. |
| `POST` | `/projects/{p}/jobs?uploadType=resumable` | Initiate a resumable session; returns 200 + `Location` + `X-GUploader-UploadID`. |
| `PUT` | `/projects/{p}/jobs?upload_id=<session>` | Append a chunk or query session status; returns 308 partial or 200 final. |

Mounted alongside the existing data-plane `jobs_router` in
[`api/app.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/api/app.py). The
`GzipRequestMiddleware` chain leaves multipart and binary bodies
untouched (the gzip middleware only inflates `Content-Encoding:
gzip` bodies — verified against the upload tests).

### 2. Multipart parsing via the stdlib `email` package

The `multipart/related` MIME shape is structurally identical to the
multipart framing the stdlib `email.parser.BytesParser` already
handles. We do **not** take a dependency on `python-multipart`
because that package is for `multipart/form-data` (named-field,
positional metadata in `Content-Disposition`) — a completely
different wire format from `multipart/related` (positional parts,
type-distinguished). Mixing them would either misparse the BQ
client's upload envelopes or require a per-format dispatch.

The parser walks the two parts in declaration order. The first part
must declare a JSON content type and parses as the `Job` resource
(specifically `configuration.load`). The second part carries the
media bytes; its declared `Content-Type` is validated against a
whitelist (`application/octet-stream`, `text/csv`,
`application/json`, `application/avro`, `application/x-parquet`,
`application/x-orc`, plus a few common variants) so the server
can't be coerced into materialising an arbitrary MIME envelope
inside the staging directory.

### 3. In-memory resumable session manager

[`src/bqemulator/jobs/upload_session_manager.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/upload_session_manager.py)
holds the active sessions in a thread-safe dict. Each session owns
a per-session temp file under `Settings.upload_staging_dir` (or the
system tempdir if unset). The manager enforces three security
boundaries:

- **`upload_id` shape** — `^[A-Za-z0-9_-]{8,64}$`. The value flows
  into a filesystem path so a strict character set closes the
  path-traversal blast radius. Anything outside the pattern returns
  404 (not 400 — leaking "you matched the wrong pattern" gives the
  attacker information). UUID hex output already conforms.
- **`upload_max_bytes` cap** — checked on every append by computing
  the prospective new offset; an over-cap chunk raises before any
  bytes touch disk. The declared `Content-Range` total is also
  validated.
- **TTL eviction** — sessions older than
  `Settings.upload_session_ttl_seconds` (default 1 hour) are evicted
  lazily on the next call that touches the manager. Eviction
  unlinks the staging file, preventing orphaned bytes from
  accumulating on a CI runner.

Session state is **process-local**. A restart drops every in-progress
upload — see the new "Durable upload session state" section in
[`out-of-scope.md`](../reference/out-of-scope.md), mirroring the
existing "Durable Storage Write API stream state" exclusion (ADR
0013).

### 4. Temp-file materialisation, then call `execute_load_job`

Once the multipart body is decoded (single-shot) or the final
resumable chunk arrives, the handler synthesises a `file://`
`sourceUris` entry pointing at the staging path and invokes
`execute_load_job` with the same configuration the client supplied.
The executor's existing `_resolve_uri` handles `file://` URIs
uniformly — no new code paths in the load executor itself; the
upload host is purely a translation layer.

Cleanup runs in a `finally` arm regardless of load outcome
(success, executor exception, request abort). An integration test
drives a deliberate schema-mismatch failure and asserts the staging
directory is empty afterward (pinned at
[`test_temp_file_cleaned_up_on_load_failure`](https://github.com/jjviscomi/bqemulator/blob/main/tests/integration/test_upload_endpoints.py)).

### 5. Settings additions

| Setting | Default | Reason |
|---|---|---|
| `upload_max_bytes` | 1 GiB | BigQuery's production cap is 5 TiB; the emulator default keeps CI runs bounded. |
| `upload_session_ttl_seconds` | 3600 (1 hour) | Long enough that a slow uploader on a flaky network finishes; short enough that a leaked session doesn't accumulate disk forever. |
| `upload_staging_dir` | `None` (system tempdir) | Operators with persistent data dirs can pin sessions under that dir; the default plays well with ephemeral containers. |

## Consequences

### Positive

- The Python `load_table_from_file(io.BytesIO(...))` idiom works
  end-to-end against the emulator. Same for the Node `table.load(stream)`,
  Go `Loader.From(reader)`, and Java `BigQuery.writer(...)` APIs.
- The four-language E2E suite now exercises the standard load path
  rather than the synthetic `gs://` flow — future load-protocol
  regressions in the upstream client libraries surface in CI rather
  than at user runtime.
- The single goccy gap left on the load axis (G-4) is closed; the
  remaining 12 gaps are independent of the load surface.

### Negative

- New attack surface: the upload host accepts arbitrary file bytes.
  Mitigated by (a) the `upload_max_bytes` size cap enforced before
  disk write, (b) the `upload_id` character-set validator, (c) the
  multipart media-type whitelist, (d) staging directory ownership
  and permissions inherited from the OS tempdir.
  `/security-review` ran against this PR (see commit log) — no
  unresolved findings.
- Upload session state is in-memory only. A pod restart mid-upload
  forces the client to restart the upload from offset 0. Operators
  running long-running CI emulator instances may want to pin
  `upload_session_ttl_seconds` higher; the cap is 24 hours.

### Neutral

- No new third-party dependencies. Multipart parsing uses the
  stdlib `email` package; in-memory state uses `threading.Lock`
  (already in the project for `WriteStreamManager` lifecycle).

## Alternatives considered

1. **Proxy upload requests to a real GCS-compatible emulator
   (e.g., fake-gcs-server).** Rejected: adds an external process to
   the container, breaks the offline-first charter, and provides no
   additional fidelity since the load executor already speaks
   `file://` URIs natively.
2. **Decline upload support entirely and document the workaround.**
   Rejected: every user who upgrades the emulator hits the same
   404, then has to rewrite their load code. The cost of supporting
   the protocol is a single router file plus a session manager;
   the cost of forcing every user to rewrite is paid forever.
3. **Persist upload session state to disk.** Rejected per
   [out-of-scope.md](../reference/out-of-scope.md)'s ephemeral-by-
   default precedent. The Storage Write API exclusion (ADR 0013)
   sets the same expectation for the gRPC streaming surface; the
   upload host's in-memory semantics are consistent.
4. **Hand-roll a multipart/related parser.** Rejected: the stdlib
   `email.parser.BytesParser` handles the RFC 2387 framing
   correctly (including nested boundaries, content-transfer-encoding,
   and the terminating `--<boundary>--` line). Re-implementing it
   is wasteful and error-prone.
