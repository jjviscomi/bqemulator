# Reference Avro fixtures (G3 / ADR 0030)

Hand-authored Apache Avro Object Container Files (OCF — `Obj\x01`
header + embedded schema + sync marker + compressed data blocks) the
integration suite uses as the source-of-truth contract for the
BigQuery → Avro schema mapping.

## What's here

| File | Conformance fixture | Purpose |
|---|---|---|
| `read_session_avro_basic.avro` | `read_session_avro_basic` | 3-row INT64+STRING happy path. |
| `read_session_avro_multi_stream.avro` | `read_session_avro_multi_stream` | 20-row INT64+STRING, exercises chunking. |
| `read_session_avro_all_types.avro` | `read_session_avro_all_types` | INT64+FLOAT64+STRING+BOOL+NUMERIC+DATE+TIMESTAMP. |
| `read_session_avro_nested_struct.avro` | `read_session_avro_nested_struct` | STRUCT + ARRAY columns. |
| `read_session_avro_with_projection.avro` | `read_session_avro_with_projection` | Projected subset (`a`, `c`). |
| `read_session_avro_split_read_stream.avro` | `read_session_avro_split_read_stream` | SplitReadStream parent shape. |

## Distinction vs. wire-format bytes

These OCF files are **NOT** the same as the bytes the Storage Read
API carries on the wire. The Storage Read API ships "schema-once on
the session, naked binary rows per response chunk" — every
ReadRowsResponse carries `serialized_binary_rows` with no Avro
container, no embedded schema, no sync marker. OCF files **DO**
carry all three.

The two purposes the OCF files serve:

1. **Schema-converter sanity baseline** — the integration test
   `test_avro_schema_converter_against_reference_file` reads each
   file's embedded schema and asserts the emulator's
   `arrow_schema_to_avro_json` produces a structurally equal schema
   (via `fastavro.parse_schema` canonical equality). Catches
   schema-converter drift independently of the wire-format work.
2. **Cross-implementation interop check** — the canonical Apache
   Avro implementation (`avro-tools`) reads these files, proving a
   second, independent Avro decoder accepts what the emulator's
   schema converter produces.

## Regenerating

Re-run after any change to the schema converter or after appending a
new entry to `_schemas.py`:

```bash
make generate-avro-fixtures
```

or equivalently:

```bash
python scripts/generate_avro_fixtures.py
```

The generator script lives at
[`scripts/generate_avro_fixtures.py`](../../../scripts/generate_avro_fixtures.py).
