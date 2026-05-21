# gRPC-shape conformance corpus

The gRPC corpus is a sibling of [`sql_corpus/`](../sql_corpus/) (SQL
row+schema diff) and [`http_corpus/`](../http_corpus/) (REST body
structural-subset diff). It exercises the gRPC wire-format shapes
on the BigQuery Storage Read API (``BigQueryRead``) and Storage
Write API (``BigQueryWrite``) that the deserialised-Python-object
assertions in `tests/integration/test_storage_*.py` cannot express.
Locked by P3.d (v1.0 confidence plan).

## Layout

```
tests/conformance/grpc_corpus/
    storage_read/
        <fixture>/
            setup.sql               # optional — table seed (via REST)
            setup_requests.json     # optional — REST setup chain
            request.json            # gRPC call sequence
            expected_response.json  # recorded baseline
    storage_write/
        <fixture>/
            ...
```

## Fixture shape

### `request.json`

```json
{
  "fixture_version": 1,
  "calls": [
    {
      "method": "BigQueryRead.CreateReadSession",
      "request": {
        "parent": "projects/${PROJECT}",
        "read_session": {
          "table": "projects/${PROJECT}/datasets/${DATASET_ID}/tables/data",
          "data_format": "ARROW"
        },
        "max_stream_count": 1
      },
      "capture": {
        "STREAM_NAME": "streams.0.name"
      }
    },
    {
      "method": "BigQueryRead.ReadRows",
      "request": {
        "read_stream": "${STREAM_NAME}"
      }
    }
  ]
}
```

- ``method`` — fully-qualified ``service.method``. The framework
  derives the gRPC kind (unary / server_stream / bidi_stream) from
  :data:`DEFAULT_METHOD_KIND`; override with an explicit ``kind`` if
  needed.
- ``request`` / ``requests`` — JSON payload(s). Deserialised back
  into the matching proto-plus message via ``from_json``. For
  bidi-stream calls (currently only ``BigQueryWrite.AppendRows``) use
  ``requests`` as an ordered list.
- ``capture`` — dotted-path → ``${UPPER_TOKEN}`` map. The first
  response message is the source. Useful for chaining (stream name
  from ``CreateReadSession`` → ``read_stream`` on ``ReadRows``).

### `expected_response.json`

```json
{
  "fixture_version": 1,
  "recorded_at": "2026-05-19T...",
  "bigquery": {"project": "...", "location": "US"},
  "calls": [
    {
      "method": "BigQueryRead.CreateReadSession",
      "status": "OK",
      "response": {
        "name": "<*>",
        "table": "...",
        "data_format": "ARROW",
        "arrow_schema": {"serialized_schema": "<*>"},
        "streams": [{"name": "<*>"}]
      }
    },
    {
      "method": "BigQueryRead.ReadRows",
      "status": "OK",
      "responses": [
        {
          "arrow_record_batch": {
            "serialized_record_batch": "<*>",
            "row_count": "10"
          },
          "row_count": "10"
        }
      ]
    }
  ]
}
```

- ``status`` — gRPC status code name (``OK`` / ``NOT_FOUND`` /
  ``INVALID_ARGUMENT`` / ``ALREADY_EXISTS`` / ``OUT_OF_RANGE`` /
  ``FAILED_PRECONDITION``).
- ``response`` (unary) / ``responses`` (server / bidi stream) —
  proto-as-JSON dict(s).
- ``error_message`` — when status != OK, the runner asserts the
  emulator's error message *contains* the recorded text.
- ``"<*>"`` (WILDCARD) — value is server-generated (Arrow IPC
  bytes, stream names, timestamps); the comparator only checks
  that the key is present.

## Recording

```bash
# One-time auth setup
gcloud auth application-default login

# Record all fixtures (skips ones already recorded)
python scripts/record_grpc_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" \
    --location US

# Force-re-record a single fixture
python scripts/record_grpc_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" \
    --filter storage_read/sr_create_session_one_stream \
    --force
```

The recorder authenticates against real BigQuery via ADC and points
its gRPC channel at ``bigquerystorage.googleapis.com:443``.
Server-generated opaque values (stream names, Arrow IPC bytes,
timestamps) are masked to ``"<*>"`` in the recorded baseline so
the comparator matches structurally.

## Running

```bash
make test-conformance
# or
pytest tests/conformance -m conformance -k grpc_corpus
```

## Comparison contract

The comparator at
[`_grpc_comparison.py`](../_grpc_comparison.py) runs **structural
subset** matching:

- Every key in the recorded baseline must be present in the
  emulator's response (unless the recorded value is ``WILDCARD``).
- Extra keys in the emulator's response are tolerated — BigQuery
  keeps adding fields to its wire format and pinning every key
  would break the corpus on every BQ minor release.
- Lists must be the same length; each element is diffed
  recursively.
- ``status`` is matched exactly.
- ``error_message`` (when present) uses a substring-contains
  assertion — real BQ's error wording varies between recordings
  (timestamps, generated IDs), but the structural envelope must
  hold.
