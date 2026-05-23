# ADR 0027: Load Avro/ORC + Extract Avro (G1)

- **Status**: Accepted

## Context

The v1.0 competitor-parity workstream G1 closes three rows of the
[goccy `bigquery-emulator` feature
matrix](https://github.com/goccy/bigquery-emulator/blob/main/FEATURE.md)
that bqemulator missed before this session:

| Gap | Before | After |
|---|---|---|
| **G-5** Load format: Avro | ❌ `UnsupportedFeatureError` at the executor | ✅ |
| **G-6** Load format: ORC | ❌ `UnsupportedFeatureError` at the executor | ✅ |
| **G-7** Extract format: Avro | ❌ `InvalidQueryError("Unknown destination format")` | ✅ |

The motivating use case is Java BigQuery clients (Hadoop / Hive / Trino
/ Presto migrations) that default to Avro for schema-preserving wire
format and to ORC for the legacy Hadoop ecosystem. Before G1, a Java
integration test that loaded from an `.avro` URI surfaced
`UnsupportedFeatureError` and the test author had to hand-convert
to Parquet. After G1, the same code runs unchanged.

The constraints to satisfy:

1. **Boot performance + offline tolerance** — engine startup must not
   network-fetch a DuckDB extension every time, and must not crash
   when the extension repository is unreachable (constrained
   deployments, air-gapped CI). Contrast with the spatial extension
   (ADR's not numbered but the engine code at
   [`engine.py:_load_spatial`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/storage/engine.py))
   which is a hard fail because GEOGRAPHY semantics cannot work
   without it.
2. **Coverage** — every new branch hits the ≥90% line+branch coverage
   gate.
3. **Conformance shape** — recorded against real BigQuery via the
   existing
   [HTTP corpus framework](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/http_corpus/README.md)
   (P2.f). Hand-authored baselines are explicitly forbidden by
   ADR 0022 §2.
4. **Four-language E2E** — Python / Node / Go / Java suites all
   exercise the load + extract surfaces against a fresh container
   (AGENTS.md non-negotiable; Java gets the third ORC test because
   ORC is most common in the Java/Hadoop ecosystem).

## Decisions

### 1. Avro: DuckDB's `avro` extension for both read and write

DuckDB 1.5+ ships an `avro` extension at
`http://extensions.duckdb.org/v1.5.2/<platform>/avro.duckdb_extension.gz`
that provides:

- `SELECT * FROM read_avro('<path>')` for reads, and
- `COPY (<query>) TO '<path>' (FORMAT AVRO)` for writes.

The extension was verified to support both read and write of
records-of-records (BigQuery STRUCT round-trips), Avro logical types
(decimal, date, timestamp-millis, timestamp-micros), and nullable
unions (`["null", "<T>"]`).

Both load + extract executor branches consult the same DuckDB
function family, so a single best-effort `INSTALL avro; LOAD avro`
at engine boot covers them. The install is **best-effort**: a
failure logs a warning and continues, then the at-query SELECT or
COPY either succeeds (DuckDB's runtime autoload picks up the
extension if it later becomes available) or fails with a clear
`Catalog Error:... is not in the catalog, but it exists in the
avro extension` envelope that the executor's
`_is_missing_extension_error` classifier translates to
`UnsupportedFeatureError` for the client.

### 2. ORC: Python `pyorc` package via Arrow bridge

The DuckDB community ORC extension is **not** packaged for
darwin-arm64 in the 1.5.2 release (404 at the extension repository
URL). The choices were:

- **Option A: Wait for upstream packaging.** Blocks the G-6 row
  indefinitely; v1.0 has shipped on the v1-confidence-plan
  timeline.
- **Option B (chosen): Route ORC reads through the Apache `pyorc`
  package** (installed via the new optional `[orc]` extra). The
  reader in `src/bqemulator/jobs/orc_reader.py` parses an ORC file
  into a `pyarrow.Table` and the executor inserts via DuckDB's
  `register(...)` + `INSERT INTO... SELECT * FROM <view>`.
- **Option C: Hand-build an ORC reader.** Multi-week scope; ORC's
  on-disk format (stripes, footer, postscript, compression codecs)
  is fully specified but rebuilding it is unjustified when
  `pyorc` exists and is maintained.

Option B is bounded scope (~150 LoC), depends on a maintained
library, and degrades cleanly when the optional extra isn't
installed (`UnsupportedFeatureError` with actionable remediation in
the error message).

### 3. ORC writes are NOT supported (out-of-scope)

BigQuery itself does **not** support ORC as a destination extract
format ([BigQuery extract docs:
formats](https://cloud.google.com/bigquery/docs/exporting-data#export_formats_and_compression_types)
list AVRO, CSV, JSON, PARQUET only). Adding ORC write would put
the emulator *ahead* of BigQuery on a surface where parity matters
— a user who extracts to ORC against the emulator and then tries
to repeat the workflow on the real service would get a surprising
failure.

We therefore pin ORC extract as out-of-scope in
[`docs/reference/out-of-scope.md`](../reference/out-of-scope.md)
with the rationale above. The workaround for users who genuinely
need ORC output is to extract to Parquet and run a downstream
conversion via `pyorc` or `pyarrow`.

### 4. Configuration flag: `enable_format_extensions`

Defaults to `True`. When `False`, the engine boot skips the
`INSTALL avro; LOAD avro` calls entirely — useful in constrained
deployments that cannot reach `extensions.duckdb.org`. The ORC
path is unaffected because it uses `pyorc`, not a DuckDB
extension. Exposed as `BQEMU_ENABLE_FORMAT_EXTENSIONS` env var via
the existing `Settings` configuration surface.

## Consequences

### Capability matrix shifts

| Surface | Before G1 | After G1 |
|---|---|---|
| Load CSV | ✅ | ✅ |
| Load NEWLINE_DELIMITED_JSON | ✅ | ✅ |
| Load PARQUET | ✅ | ✅ |
| Load AVRO | ❌ | ✅ |
| Load ORC | ❌ | ✅ |
| Extract CSV | ✅ | ✅ |
| Extract NEWLINE_DELIMITED_JSON | ✅ | ✅ |
| Extract PARQUET | ✅ | ✅ |
| Extract AVRO | ❌ | ✅ |
| Extract ORC | ❌ (matches BigQuery) | ❌ (matches BigQuery) |

After G1, bqemulator becomes a **strict superset** of both goccy
and BigQuery on the load/extract format axis: equal on the
intersect-with-BigQuery set, ahead of goccy on Parquet extract +
Avro/ORC load + Avro extract.

### Coverage + test surface

- 4 new unit tests in
  [`tests/unit/storage/test_engine_format_extensions.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/storage/test_engine_format_extensions.py)
  for the engine flag + best-effort load contract.
- 5 new unit tests in
  [`tests/unit/jobs/test_executor.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/jobs/test_executor.py)
  for the `_is_missing_extension_error` classifier.
- 12 new integration tests in
  [`tests/integration/test_load_avro_orc.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/integration/test_load_avro_orc.py)
  covering Avro/ORC load basic + nested + missing-file + round-trip
 + Avro extract + ORC reader unit tests + Settings flag.
- 8 new conformance fixtures under
  [`tests/conformance/http_corpus/jobs/`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/http_corpus/jobs/)
  (recorded against real BigQuery; the recorder runs as an
  operator-side step because the fixture-recording flow needs
  the operator's BQ ADC + GCS access).
- 9 new four-language E2E tests
  (2 × Python/Node/Go + 3 × Java including ORC).

### Optional dependencies

A new `[orc]` extra in [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml)
gates the `pyorc` dependency. The `[all]` umbrella extra picks it
up automatically; existing user installs of `bqemulator[avro]` keep
working without ORC support.

### Avro logical-type coverage

The recorded conformance fixture set covers these Avro logical
types: `decimal`, `date`, `timestamp-millis`, `timestamp-micros`.
Logical types BigQuery itself doesn't surface (`uuid`,
`local-timestamp-*`, `time-millis`/`time-micros` at full precision)
are out of scope by definition — BigQuery's load path either
coerces to a supported type or rejects them, and the emulator
matches that surface.

### Error envelope

Two new classifier branches in the executor (`_is_missing_extension_error`
for Avro load + Avro extract) plus a new helper module
(`src/bqemulator/jobs/orc_reader.py`). Both branches preserve the
existing `error_mapper` chain — missing-file / schema-mismatch errors
flow through the standard BigQuery-shape translator unchanged.

## Alternatives considered

- **Apache Arrow `pyarrow.avro` for Avro read/write** — `pyarrow`
  lacks a stable Avro module (the experimental
  `pyarrow.dataset.avro` reader is not officially supported and was
  removed in recent releases). Rejected.
- **`fastavro` for Avro write fallback** — a viable fallback if
  DuckDB's COPY TO AVRO ever regresses, but adds a second code
  path with subtle Arrow-→-Avro-schema mapping logic. Tabled as a
  fallback to wire in only if the DuckDB path fails in CI.
- **A custom ORC codec** — multi-day scope; ORC's documented but
  intricate stripe/footer format does not justify reinvention when
  `pyorc` exists.
- **Routing both formats through `pyarrow.dataset`** — `pyarrow`
  surfaces `read_orc` (via the `pyorc` package internally for ORC,
  or its own C++ implementation when available). Rejected for ORC
  because the `pyarrow` ORC binding is not built by default in the
  pyarrow wheels and would force operators to compile from source.

## Related work

- **G2** (multipart/resumable upload endpoints) — closes the
  load-from-local-file path the four client libraries use by
  default. Independent of G1.
- **G3** (Storage Read Avro encoding) — extends the Storage Read
  API beyond Arrow. Independent.

## References

- [DuckDB `avro` extension](https://duckdb.org/docs/extensions/avro)
- [BigQuery extract destination formats](https://cloud.google.com/bigquery/docs/exporting-data#export_formats_and_compression_types)
- [Apache `pyorc`](https://github.com/noirello/pyorc)
- [BigQuery Avro export details](https://cloud.google.com/bigquery/docs/exporting-data#avro_export_details)
- [goccy `bigquery-emulator` FEATURE.md](https://github.com/goccy/bigquery-emulator/blob/main/FEATURE.md)
