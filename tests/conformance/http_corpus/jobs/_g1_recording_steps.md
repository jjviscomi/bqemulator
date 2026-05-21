# G1 conformance fixture recording — operator-side checklist

The eight G1 fixtures (`load_avro_*`, `load_orc_*`,
`extract_avro_*`) reference `gs://${GCS_BUCKET}/g1/<name>.<ext>`
URIs that the **operator** must stage in a GCS bucket the recorder's
ADC can read/write before the first `record_http_fixtures.py` run.
Once staged, the recorder runs unattended.

## Prerequisites

1. **BigQuery ADC** — `gcloud auth application-default login` against
   the project named by `BQEMU_CONFORMANCE_PROJECT`.
2. **A GCS bucket the ADC principal can read+write.** Export its
   name as `BQEMU_CONFORMANCE_GCS_BUCKET` (no `gs://` prefix).
3. **Python deps** — `pip install bqemulator[avro,orc]` plus
   whatever the recorder script imports
   ([`scripts/record_http_fixtures.py`](../../../../scripts/record_http_fixtures.py)).

## Stage the source files

Run this one-shot Python script from the repo root:

```python
# scripts/_stage_g1_fixtures.py — operator-side, not committed yet.
import io, os
from datetime import date, datetime
from decimal import Decimal

import fastavro, pyorc
from google.cloud import storage

bucket_name = os.environ["BQEMU_CONFORMANCE_GCS_BUCKET"]
client = storage.Client()
bucket = client.bucket(bucket_name)

def upload(blob_name: str, payload: bytes) -> None:
    bucket.blob(f"g1/{blob_name}").upload_from_string(payload)

# --- load_avro_basic ---
schema = fastavro.parse_schema({
    "type": "record", "name": "Item",
    "fields": [
        {"name": "id", "type": "long"},
        {"name": "name", "type": ["null", "string"], "default": None},
    ],
})
buf = io.BytesIO()
fastavro.writer(buf, schema, [
    {"id": 1, "name": "alpha"},
    {"id": 2, "name": "beta"},
    {"id": 3, "name": "gamma"},
])
upload("load_avro_basic.avro", buf.getvalue())

# --- load_avro_nested_record ---
schema = fastavro.parse_schema({
    "type": "record", "name": "Person",
    "fields": [
        {"name": "id", "type": "long"},
        {"name": "name", "type": "string"},
        {"name": "addr", "type": {
            "type": "record", "name": "Address",
            "fields": [
                {"name": "city", "type": "string"},
                {"name": "zip", "type": "string"},
            ],
        }},
    ],
})
buf = io.BytesIO()
fastavro.writer(buf, schema, [
    {"id": 1, "name": "Ada", "addr": {"city": "London", "zip": "NW1"}},
    {"id": 2, "name": "Linus", "addr": {"city": "Helsinki", "zip": "00100"}},
])
upload("load_avro_nested_record.avro", buf.getvalue())

# --- load_avro_logical_decimal ---
schema = fastavro.parse_schema({
    "type": "record", "name": "Amount",
    "fields": [
        {"name": "id", "type": "long"},
        {"name": "value", "type": {
            "type": "bytes", "logicalType": "decimal",
            "precision": 38, "scale": 9,
        }},
    ],
})
buf = io.BytesIO()
fastavro.writer(buf, schema, [
    {"id": 1, "value": Decimal("123.456789000")},
    {"id": 2, "value": Decimal("-0.000000001")},
])
upload("load_avro_logical_decimal.avro", buf.getvalue())

# --- load_orc_basic ---
buf = io.BytesIO()
w = pyorc.Writer(buf, "struct<id:bigint,name:string>")
for r in [(1, "alpha"), (2, "beta"), (3, "gamma")]:
    w.write(r)
w.close()
upload("load_orc_basic.orc", buf.getvalue())

# --- load_orc_nested ---
buf = io.BytesIO()
w = pyorc.Writer(buf, "struct<id:bigint,name:string,addr:struct<city:string,zip:string>>")
w.write((1, "Ada", ("London", "NW1")))
w.write((2, "Linus", ("Helsinki", "00100")))
w.close()
upload("load_orc_nested.orc", buf.getvalue())

# --- load_avro_invalid_file (intentionally NOT an avro file) ---
upload("load_avro_invalid_file.txt", b"this is plain text, not avro\n")
```

`extract_avro_basic` and `extract_avro_round_trip` do NOT need any
pre-staged input — the recorder writes the destination Avro file as a
side-effect of the canonical jobs.insert. The round-trip fixture's
`setup_requests.json` runs the extract first; the canonical request
loads the extracted file back in.

## Record

```bash
export BQEMU_CONFORMANCE_PROJECT=<your-project>
export BQEMU_CONFORMANCE_GCS_BUCKET=<your-bucket>
export GOOGLE_APPLICATION_CREDENTIALS=<path-to-key.json>

python scripts/record_http_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" \
    --filter load_avro_ \
    --filter load_orc_ \
    --filter extract_avro_
```

Each fixture's `expected_response.json` is written next to its
`request.json`. The conformance runner picks them up automatically on
the next `make test-conformance` (or `pytest tests/conformance -m
conformance`) run.

## Verify

```bash
pytest tests/conformance/test_http_corpus.py \
    -m conformance \
    -k "load_avro_ or load_orc_ or extract_avro_" -v
```

All eight should pass. Any divergence between the emulator's response
shape and the recorded BigQuery shape gets pinned via
[`tests/conformance/divergences.py`](../../divergences.py) — see ADR
0023 for the divergence-baseline policy.
