# ADR 0049: Upload media part accepts any non-composite Content-Type

- **Status**: Accepted (supersedes the media-type whitelist in
  [ADR 0029](0029-upload-host-endpoints.md) Decision #2)

## Context

[ADR 0029](0029-upload-host-endpoints.md) Decision #2 validated the
declared `Content-Type` of a `multipart/related` upload's media part
against a whitelist of load-format types (`text/csv`, `application/json`,
`application/x-parquet`, `application/octet-stream`, and a few variants)
"so the server can't be coerced into materialising an arbitrary MIME
envelope inside the staging directory".

Real BigQuery has no such rule. The `jobs.insert` method in the BigQuery
v2 discovery document declares `mediaUpload.accept: ["*/*"]`, and the
load is always parsed per `configuration.load.sourceFormat`, never per
the media part's label. The official clients depend on that, and what
they put on the wire was verified against their sources
(`google-cloud-bigquery` 3.42, `@google-cloud/bigquery` 8.3,
`cloud.google.com/go/bigquery` 1.61, `google-cloud-bigquery` for Java
2.40, and the Cloud SDK `bq` tool):

| Client | When it sends `uploadType=multipart` | Media part `Content-Type` |
|---|---|---|
| Python | size known and under 5 MiB: `load_table_from_file(..., size=n)`, `load_table_from_json`, `load_table_from_dataframe` | `*/*` (`_GENERIC_CONTENT_TYPE`) |
| Node.js | always (`createWriteStream`, `load(localPath)`) | `application/octet-stream` |
| Go | payload under the upload chunk size (16 MiB default) | `http.DetectContentType` of the first 512 bytes |
| Java | never (resumable only) | — |
| `bq` CLI | only for an empty file (resumable otherwise) | `application/octet-stream` |

The whitelist therefore rejected every Python multipart load with
`400 multipart upload's second part has unsupported Content-Type '*/*'`,
and rejected Go loads whose first bytes happen to match a non-text
signature: a CSV whose header starts with `BM` sniffs as `image/bmp`, one
that starts with `<b>` or `<p>` sniffs as `text/html`, gzip data as
`application/x-gzip`. The suites missed both: the Python e2e "multipart"
test passed an unsized `BytesIO`, which the client always sends
resumable, and the Go tests' payloads all sniffed as `text/plain`.

The whitelist also did not protect the property it was written for. Any
bytes at all could already be staged under `application/octet-stream`,
and the parse is selected by `sourceFormat` through its own closed set of
loaders. The only property the declared type genuinely affects is
structural: the stdlib `email` parser chosen in ADR 0029 interprets a
part declared `multipart/*` or `message/*` as nested MIME entities. For a
well-formed nested entity it returns no payload at all (an empty staged
file and a silent zero-row load); for a `multipart/*` part whose body
does not contain its own boundary it keeps the CRLF that belongs to the
enclosing delimiter on the payload (two stray bytes after a Parquet
footer).

## Decisions

### 1. Accept every discrete media type

[`_parse_media_part`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/api/routes/upload.py)
accepts any media part whose top-level type is not composite, including
`*/*`, a missing header (RFC 2045 default `text/plain`), and arbitrary
sniffed types. The bytes are staged verbatim and parsed per
`sourceFormat` exactly as before.

### 2. Reject composite top-level types

A media part declared `multipart/*` or `message/*` (RFC 2046 §5, the two
top-level types the `email` parser decomposes) is rejected with
`400 invalidQuery` naming the type, instead of staging the parser's
decomposition. Real BigQuery would read such a part as opaque bytes; no
client library emits one, so the emulator documents the gap in
[out-of-scope.md](../reference/out-of-scope.md#composite-media-part-content-types-on-multipart-uploads)
rather than replacing the parser (see Alternative 3).

### 3. Pin byte-exactness, not labels

A Hypothesis property test drives arbitrary binary payloads (bare CR / LF,
CRLF, NUL, high bytes, delimiter-like prefixes) under arbitrary discrete
types through the parser using the `google-resumable-media` framing and
asserts the staged bytes equal the input. A conformance fixture replays
the exact multipart request `load_table_from_json` sends. The e2e suites
exercise the multipart path each client actually takes: Python
`load_table_from_json` and sized `load_table_from_file` (CSV and Parquet),
Node `createWriteStream`, and Go loads sniffed as `text/plain`,
`image/bmp`, and `text/html`.

## Consequences

### Positive

- `load_table_from_json`, `load_table_from_dataframe`, and sized
  `load_table_from_file` calls from the Python client work against the
  emulator, as do Go loads regardless of what their first bytes sniff as.
- The emulator accepts the same media types BigQuery does; the
  remaining controls on the upload surface (size cap before disk write,
  `upload_id` character set, staging-directory ownership, `sourceFormat`
  dispatch) are unchanged.

### Negative

- A media part declared with a composite type is rejected where real
  BigQuery would accept it. No official client produces one.

### Neutral

- The resumable protocol is unaffected: its chunk bodies never carried a
  media-part type to check.

## Alternatives considered

1. **Add `*/*` to the whitelist.** Fixes the Python client only; Go loads
   still fail on data-dependent sniff results, so the failure stays
   reachable by an unmodified client.
2. **Add Go's sniff results to the whitelist.** `http.DetectContentType`
   recognises dozens of signatures and the set changes across Go
   releases; any finite list re-creates the data-dependent failure for
   the next signature.
3. **Replace the stdlib parser with a byte-level part splitter** so a
   composite-labelled part could be returned verbatim. Rejected for the
   reason ADR 0029 Alternative 4 gives for hand-rolling the parser, with
   no client that would benefit.
