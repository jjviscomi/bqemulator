# ADR 0030: Storage Read API — Avro output format (G3)

- **Status**: Accepted

## Context

The v1.0 competitor-parity workstream G3 closes the single row of the
[goccy `bigquery-emulator` feature
matrix](https://github.com/goccy/bigquery-emulator/blob/main/docs/feature-support.md)
where bqemulator's Storage Read API supported only Arrow IPC while
goccy supported both Arrow and Apache Avro:

| Gap | Before | After |
|---|---|---|
| **G-8** Storage Read API — Avro output | ❌ servicer hard-coded `data_format=types.DataFormat.ARROW` at [`read_servicer.py:283`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/grpc_api/read_servicer.py) | ✅ |

The motivating use case: **the Java BigQuery Storage Read client
defaults to Avro**, not Arrow. A Java consumer that uses
`BigQueryReadClient.create().createReadSession(...)` without
explicitly setting `DataFormat.ARROW` requests Avro and (before G3)
got a gRPC error from the emulator. Python / Go / Node default to
Arrow so the gap was invisible there; in Java, it was blocking.

The wire-format contract the Storage Read API obeys (per
[BigQuery's Storage Read API
docs](https://docs.cloud.google.com/bigquery/docs/reference/storage#avro)):

* `ReadSession.avro_schema.schema` carries the writer schema as a
  JSON string (Avro schema is itself JSON).
* `ReadRowsResponse.avro_rows.serialized_binary_rows` carries the
  per-chunk row bytes encoded via Avro's binary encoding,
  back-to-back. **NO Avro Object Container File (OCF) header** — no
  `Obj\x01` magic, no embedded schema, no sync marker per chunk. The
  schema is sent once on the session; each row block is naked.

The constraints to satisfy:

1. **No drift on the Arrow path.** Phase 4 + P3.d shipped the Arrow
   surface; the Java default has been silently broken for everyone
   else. The Arrow path must stay byte-identical to its post-P3.d
   shape.
2. **Real Avro decoders must accept the bytes.** The defining
   failure mode this workstream guards against is "emit proto-valid
   bytes that no Avro decoder can actually parse." Every layer (unit,
   integration, conformance, E2E) asserts at the *decoded-row* level
   via a real Avro implementation.
3. **Cross-implementation interop.** A single Python implementation
   (fastavro) can validate itself in a circle; we also need a second,
   independent Avro implementation to decode the bytes. The Apache
   Avro implementation (`avro-tools` jar; Java's
   `DataFileReader<GenericRecord>`) serves that role.
4. **Coverage** — every new branch ≥90% line + branch.
5. **Conformance shape** — recorded via the existing P3.d gRPC corpus
   framework, with the new three-layer comparator (proto envelope +
   Avro schema parse-equality + decoded-row equality).

## Decisions

### 1. fastavro as the encoder

`fastavro` (PyPI `fastavro>=1.9`) is already a soft dependency under
the `[avro]` optional extra for G1's Avro load/extract path. It
provides `fastavro.schemaless_writer` — a per-row, schema-already-
known encoder that matches the Storage Read "naked binary rows"
wire shape exactly.

Alternatives considered:

* **Apache `python-avro`** — slower (pure-Python encoder), more
  full-featured than we need. fastavro is the de-facto Python Avro
  library and ~10× faster per row.
* **Custom Avro encoder** — Avro's binary encoding is fully
  documented and not enormous, but writing and maintaining an
  encoder duplicates well-tested code with no upside.

### 2. Promote fastavro to runtime (not optional)

The Java client default is Avro; ANY deployment serving Java
consumers needs Avro support. Promoting fastavro from `[avro]` extra
to runtime means a one-time install footprint increase (~1 MB
wheel) in exchange for the canonical Java BQ Storage Read code path
working out-of-the-box.

### 3. BigQuery → Avro type mapping per Google's documented contract

The schema converter (`arrow_schema_to_avro_json`) implements
Google's documented [BigQuery → Avro export
mapping](https://docs.cloud.google.com/bigquery/docs/exporting-data#avro_export_details):

| BigQuery | Avro |
|---|---|
| INT64 | `long` |
| FLOAT64 | `double` |
| NUMERIC | `bytes` + `logicalType=decimal`, precision=38, scale=9 |
| BIGNUMERIC | `bytes` + `logicalType=decimal`, precision=76, scale=38 |
| STRING | `string` |
| BYTES | `bytes` |
| BOOL | `boolean` |
| DATE | `int` + `logicalType=date` |
| TIME | `long` + `logicalType=time-micros` |
| DATETIME | `string` (BigQuery-special — no native Avro logical type) |
| TIMESTAMP | `long` + `logicalType=timestamp-micros` |
| GEOGRAPHY | `string` (WKT encoding, per BQ docs) |
| JSON | `string` |
| RANGE\<T\> | `record` with `start`/`end` fields, recursive on T |
| INTERVAL | `string` (canonical Y-M D H:M:S form) |
| ARRAY\<T\> | `array` of T |
| STRUCT | `record` |
| nullable T | `["null", <T>]` union with `"null"` first |

Cited authoritatively in the converter's module docstring; tested
exhaustively in `tests/unit/streaming/test_avro_serializer.py`.

### 4. Servicer dispatch on the request's `data_format`

The read servicer dispatches at session-creation time:

```python
raw_format = read_session._pb.data_format  # bypass proto-plus enum warn
if raw_format in (UNSPECIFIED, ARROW):
    session_format = FORMAT_ARROW
    wire_format    = DataFormat.ARROW
elif raw_format == AVRO:
    session_format = FORMAT_AVRO
    wire_format    = DataFormat.AVRO
else:
    return INVALID_ARGUMENT("Unsupported data_format: …")
```

The chosen format lives on `ReadSessionState.data_format` (with
`avro_schema_json` pre-computed once at session creation) so every
subsequent `ReadRows` call and every `SplitReadStream` child serves
the same format without re-deriving it from the request. The state
is format-agnostic at the row layer (the Arrow table IS the
snapshot); the format-specific bytes are computed on the fly per
chunk.

Reading `_pb.data_format` (the raw protobuf int) rather than the
proto-plus enum property side-steps proto-plus's `UserWarning` on
unknown enum values, which the test runner's `filterwarnings =
["error"]` would otherwise convert into a `Unexpected
[UserWarning]` 500 error for any hand-crafted client request that
sends an out-of-range data_format byte.

### 5. Three-layer conformance comparison

The G3 fixtures land in the existing P3.d
[`grpc_corpus/`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/grpc_corpus/) directory.
The comparator gains two new helpers in
[`_grpc_comparison.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/_grpc_comparison.py):

| Layer | What it checks | How |
|---|---|---|
| **Proto envelope** | `ReadSession` + `ReadRowsResponse` proto fields match the recorded structural subset | existing P3.d `compare_grpc_calls` |
| **Avro schema** | The emulator's `avro_schema.schema` parses as valid Avro JSON AND is canonically equal to the recorded schema | new `compare_avro_schema` (uses `fastavro.parse_schema` for normalisation) |
| **Avro rows** | `fastavro.schemaless_reader` decodes the emulator's bytes AND yields a row list equal to the recorded `decoded_rows` | new `decode_and_compare_avro_rows` (with FLOAT64 / Decimal tolerance per ADR 0022 §3) |

Bytes are NOT compared (encoder freedom in varint padding / union
ordering). Decoded values ARE compared.

### 6. Cross-implementation Avro interop

The conformance comparator uses fastavro; that's a single
implementation cross-checking itself. To catch a class of drift
where the emulator emits bytes only fastavro accepts, we exercise
a second, independent Apache Avro implementation in two places:

1. **Integration:**
   `test_emulator_avro_bytes_decode_with_apache_avro_tools` uses
   the canonical Apache Avro `avro-tools` jar (`getschema` +
   `tojson`) against an emulator-emitted OCF.
2. **E2E (Java):** the Java suite's round-trip-to-`.avro` test
   uses the official Apache Avro
   `DataFileReader<GenericRecord>` (the canonical Java
   implementation, NOT fastavro) to read back what the emulator
   emitted. This is the load-bearing cross-impl proof.

The two-implementation rule MUST hold: if the emulator drifts away
from the documented wire format, at least one of these two
independent decoders will fail before any user does.

### 7. Reference files under `tests/fixtures/avro/`

Six hand-authored Avro OCF files (one per conformance fixture) live
under [`tests/fixtures/avro/`](https://github.com/jjviscomi/bqemulator/blob/main/tests/fixtures/avro/). They are
NOT the wire-format bytes the Storage Read API emits — they are
standard OCFs with embedded schemas + sync markers, used as the
source-of-truth contract for the schema converter. They are
regenerated via `make generate-avro-fixtures` from
[`scripts/generate_avro_fixtures.py`](https://github.com/jjviscomi/bqemulator/blob/main/scripts/generate_avro_fixtures.py),
which drives the emulator's own schema converter so the files stay
in lock-step with any future schema-mapping change.

## Consequences

### Positive

* **Java consumer default path works.** The canonical
  `BigQueryReadClient.create().createReadSession(...)` Java code
  runs unchanged.
* **goccy parity row G-8 closed.** Twelve gaps remain (G2–G4 etc.;
  see v1-confidence-plan).
* **Real Avro file round-trip provably works** end-to-end via the
  three integration tests + four-language E2E + cross-impl
  `avro-tools`/`DataFileReader` checks.

### Negative

* **fastavro becomes a runtime dependency.** Adds ~1 MB to the
  install footprint; pip-audit must clean it on every release.
* **Avro test surface expands.** Six new conformance fixtures, six
  reference OCFs, three load-bearing integration tests, four-language
  E2E (Java gets the most attention). Maintenance is bounded by the
  Avro spec being fully documented and stable.

### Neutral

* No changes to the Arrow path beyond what's needed to wire the
  format-branch dispatch. Existing Arrow conformance + integration +
  E2E coverage continues to assert byte-identical behaviour.

## Alternatives considered

* **Skip Java's default.** Document "set DataFormat.ARROW explicitly
  in Java" as a known limitation. Rejected: the whole point of an
  emulator is drop-in compatibility; adding "set this flag" instructions
  defeats the purpose.
* **Keep fastavro as an optional extra.** Rejected: the canonical
  Java BQ Storage code path silently breaks unless the install
  includes the extra. Either fastavro is in the runtime tree or
  Avro is documented as not-supported; the half-measure leaves
  every deployment one mistake away from a broken Java consumer.
* **Use Apache `python-avro` as the encoder.** Rejected: slower
  pure-Python encoder; fastavro is the de-facto Python Avro library
  with broader install footprint and faster per-row encoding.
* **Hand-write the Avro encoder.** Rejected: maintenance cost without
  upside; Avro's wire format is fully documented but the encoder is
  enough code to be worth re-using a maintained dependency.

## References

* [BigQuery Storage Read API — Avro
  format](https://docs.cloud.google.com/bigquery/docs/reference/storage#avro)
  — the wire-format contract.
* [BigQuery → Avro export
  mapping](https://docs.cloud.google.com/bigquery/docs/exporting-data#avro_export_details)
  — the BQ-to-Avro type table.
* [ADR 0022](0022-conformance-corpus-design.md) — recorded-baseline
  design + FLOAT64 / numeric tolerance rules.
* [ADR 0027](0027-load-extract-avro-orc.md) — sibling G1 workstream
  closing Avro/ORC on the *load/extract* axis.
* [ADR 0008](0008-snapshot-storage-read-api.md) — Storage Read API
  session-time materialisation snapshot (still authoritative).
