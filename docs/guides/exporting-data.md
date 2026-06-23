# Exporting data

Status: shipped.

The GoogleSQL `EXPORT DATA OPTIONS(...) AS query_statement` statement
writes the rows of a query to one or more files in Cloud Storage. It
runs as a **`QUERY` job** (`statementType` `EXPORT_DATA`) that returns no
result rows, so the same SQL you run in BigQuery — directly, in a
scheduled query, or inside a dbt model — works unchanged against the
emulator. See [RFC 0001](../rfcs/0001-export-data-statement.md) and
[ADR 0043](../adr/0043-export-data-statement.md) for the design.

```sql
EXPORT DATA OPTIONS (
  uri = 'gs://my-bucket/exports/customers_*.csv',
  format = 'CSV',
  overwrite = true
) AS
SELECT id, name FROM my_dataset.customers ORDER BY id;
```

## How `gs://` URIs resolve

The emulator is not a Cloud Storage emulator (see
[out-of-scope.md](../reference/out-of-scope.md#google-cloud-storage-emulation)).
Instead, `gs://` URIs resolve through the same filesystem shim that load
and extract jobs use: a `gs://bucket/object` URI maps to
`$BQEMU_GCS_LOCAL_ROOT/bucket/object` on disk. Set the root when you
start the emulator:

```bash
export BQEMU_GCS_LOCAL_ROOT=/tmp/bqemu-gcs
bqemulator start --ephemeral
```

`EXPORT DATA` creates the destination's parent directories as needed, so
`gs://my-bucket/exports/customers_000000000000.csv` lands at
`/tmp/bqemu-gcs/my-bucket/exports/customers_000000000000.csv`. A test (or
a [fake-gcs-server](../adr/0034-scio-beam-emulator-routing.md) sidecar
sharing the same root) reads the bytes straight back. Exporting a `gs://`
URI with no `BQEMU_GCS_LOCAL_ROOT` configured is rejected with
`Cannot resolve gs:// URIs without BQEMU_GCS_LOCAL_ROOT configured`.

## A first export

```python
from google.cloud import bigquery

# client points at a running emulator with BQEMU_GCS_LOCAL_ROOT set
client.query("CREATE TABLE my_dataset.customers (id INT64, name STRING)").result()
client.query(
    "INSERT INTO my_dataset.customers (id, name) "
    "VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')"
).result()

export = client.query(
    "EXPORT DATA OPTIONS ("
    "  uri = 'gs://my-bucket/exports/customers_*.csv',"
    "  format = 'CSV', overwrite = true) AS "
    "SELECT id, name FROM my_dataset.customers ORDER BY id"
)
export.result()                      # blocks until the job is DONE
assert export.statement_type == "EXPORT_DATA"
```

The single `*` wildcard expands to a 12-digit counter, so a small result
produces one file, `customers_000000000000.csv`, with a header row:

```text
id,name
1,alpha
2,beta
3,gamma
```

A complete, CI-verified version of this flow — starting an in-process
emulator, exporting, and reading the file back — lives in
[`docs/examples/python/export-to-gcs/`](../examples/python/export-to-gcs/README.md).

## OPTIONS

| Option | Type | Applies to | Default | Notes |
|---|---|---|---|---|
| `uri` | STRING | all | — (required) | `gs://…`; zero or one `*` wildcard (see [File naming and sharding](#file-naming-and-sharding)). |
| `format` | STRING | all | `CSV` | `CSV`, `NEWLINE_DELIMITED_JSON` (alias `JSON`), `AVRO`, `PARQUET`. `ORC` is rejected (see [Formats and compression](#formats-and-compression)). |
| `compression` | STRING | all | `NONE` | Per-format allow-lists below. AVRO compression is validated but not applied — see [Limitations](#limitations). |
| `overwrite` | BOOL | all | `false` | When `false` and a target file already exists, the export errors. |
| `header` | BOOL | CSV | `true` | Emit a header row. |
| `field_delimiter` | STRING | CSV | `,` | Single character; `tab` and `\t` resolve to a literal tab. |
| `use_avro_logical_types` | BOOL | AVRO | — | Accepted and validated, but not yet applied — see [Limitations](#limitations). |

Unknown options (`Unknown EXPORT DATA option: …`) and option/format
mismatches (`header` or `field_delimiter` on a non-CSV format,
`use_avro_logical_types` on a non-AVRO format) are rejected with a clear
`InvalidQueryError` rather than silently ignored.

## Formats and compression

Four destination formats are supported, matching BigQuery's
[documented export set](https://docs.cloud.google.com/bigquery/docs/exporting-data#export_formats_and_compression_types).
The `compression` value is validated against the chosen format:

| Format | `compression` values | Applied? |
|---|---|---|
| `CSV` | `GZIP`, `NONE` | yes |
| `NEWLINE_DELIMITED_JSON` (alias `JSON`) | `GZIP`, `NONE` | yes |
| `PARQUET` | `SNAPPY`, `GZIP`, `ZSTD`, `NONE` | yes |
| `AVRO` | `DEFLATE`, `SNAPPY`, `NONE` | **validated only — not applied** ([Limitations](#limitations)) |

`ORC` is **not** an export format. BigQuery rejects `format = 'ORC'` the
same way it rejects any unrecognised value — an invalid `format` OPTIONS
value (`invalidQuery`, HTTP 400, `location = "query"`), with the message
`'ORC' is not a valid value; failed to set 'format' in EXPORT DATA OPTIONS`.
The emulator matches this. (ORC *extract* is a separately documented
exclusion; see [out-of-scope.md](../reference/out-of-scope.md#orc-extract).)

AVRO export requires DuckDB's `avro` extension. When it is unavailable —
for example with `BQEMU_ENABLE_FORMAT_EXTENSIONS` disabled and no network
access to `extensions.duckdb.org` — an AVRO export raises a clear
`UnsupportedFeatureError`.

## File naming and sharding

- A **wildcard-free** URI (`gs://bucket/snapshot.parquet`) writes a
  single file.
- A URI with a **single `*`** shards the output: the `*` is replaced by a
  zero-based, 12-digit, left-padded counter (`…000000000000`,
  `…000000000001`, …), matching BigQuery's naming scheme. An `ORDER BY`
  in the query is preserved across shards, with rows distributed
  sequentially.
- An **empty** result still writes one file (header-only for CSV).
- More than one `*` is rejected
  (`EXPORT DATA uri may contain at most one '*' wildcard`).

Sharding is size-based. The materialized result is split into
`ceil(in-memory_size / threshold)` files, where the threshold is the
`export_shard_threshold_bytes` setting (`BQEMU_EXPORT_SHARD_THRESHOLD_BYTES`),
default **1 GiB** to match BigQuery's per-file limit. With the default,
realistic small exports produce a single `…000000000000` file. A
wildcard-free URI whose result would exceed the threshold is rejected,
mirroring BigQuery's "use a wildcard for large results" rule:

```text
Exported data exceeds the single-file size limit; use a uri with a
single '*' wildcard to shard the output across files.
```

To exercise multi-file sharding deterministically in a test, lower the
threshold so a small result splits across several files:

```bash
BQEMU_EXPORT_SHARD_THRESHOLD_BYTES=4096 bqemulator start --ephemeral
```

The per-file size uses the result's **in-memory Arrow size** as a proxy
for the compressed on-disk size, so the emulator's shard boundaries can
differ from BigQuery's at the margin (see
[ADR 0043](../adr/0043-export-data-statement.md)). The common single-file
case is recorded against real BigQuery in the conformance corpus.

## Overwrite semantics

`overwrite` defaults to `false`. When `false`, an export whose target
file already exists is rejected
(`Destination already exists and overwrite is false: …`). Set
`overwrite = true` to replace an existing file:

```sql
EXPORT DATA OPTIONS (uri = 'gs://b/out.csv', format = 'CSV', overwrite = true)
AS SELECT 1 AS a;
```

## Inside scripts

Because `EXPORT DATA` flows through the same single-statement execution
path as a standalone query job, it also works inside a
`BEGIN … END` [script](scripting.md):

```sql
BEGIN
  EXPORT DATA OPTIONS (uri = 'gs://b/s.csv', format = 'CSV')
  AS SELECT 42 AS answer;
END;
```

## Errors

The export path produces BigQuery-shaped errors for:

- a missing or empty `uri` — `Option 'uri' is missing or empty.`;
- more than one `*` wildcard;
- a wildcard-free URI whose result exceeds the size threshold;
- an invalid `format` value, including `ORC` (`invalidQuery` / HTTP 400);
- an invalid `compression` value for the chosen format;
- an unknown option, or a CSV/AVRO-only option on the wrong format;
- `overwrite = false` with an existing target;
- a `gs://` URI when `BQEMU_GCS_LOCAL_ROOT` is unset;
- `EXPORT DATA WITH CONNECTION` (external sinks are out of scope — see
  [Limitations](#limitations)).

The missing/empty-`uri` and invalid-`format` envelopes are pinned by
conformance fixtures recorded from real BigQuery.

## Limitations

- **AVRO `compression` is validated but not applied.** DuckDB's `avro`
  `COPY` writer exposes no codec option, so a value such as
  `compression = 'DEFLATE'` is accepted (and a value outside
  `DEFLATE`/`SNAPPY`/`NONE` is still rejected), but the file is written
  uncompressed. CSV, JSON, and PARQUET compression *are* applied.
- **`use_avro_logical_types` is validated but not applied.** It is
  accepted on AVRO exports (and rejected on other formats), but does not
  yet change the written schema.
- **Sharding is approximate.** Per-file sizing uses the in-memory Arrow
  size, not the compressed on-disk size, so shard boundaries can differ
  from BigQuery's at the margin. Multi-shard naming and ordering are
  exact; only the byte-threshold split point is approximate.
- **The result is materialized in memory** before being written; the
  emulator does not stream large exports.
- **`EXPORT DATA WITH CONNECTION` is out of scope.** Exporting to
  external sinks (Amazon S3, Azure Blob, Pub/Sub) is rejected with a
  clear `UnsupportedFeatureError`; the emulator exports to Cloud Storage
  only. See
  [out-of-scope.md](../reference/out-of-scope.md#export-data-with-connection-external-sinks).

## See also

- [Runnable example: export to GCS](../examples/python/export-to-gcs/README.md)
- [Loading data](loading-data.md) — the inbound counterpart (load jobs).
- [Using the `bq` CLI](using-bq-cli.md) — run `EXPORT DATA` from the CLI.
- [RFC 0001](../rfcs/0001-export-data-statement.md) /
  [ADR 0043](../adr/0043-export-data-statement.md) — design and rationale.
