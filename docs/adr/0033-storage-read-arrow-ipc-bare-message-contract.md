# ADR 0033: Storage Read API — Arrow IPC bare-message contract

- **Status**: Accepted

## Context

[Issue #15](https://github.com/jjviscomi/bqemulator/issues/15) surfaced
a wire-format mismatch in the v1.0.0 ``BigQueryRead/ReadRows`` gRPC
servicer. Real BigQuery's
[Storage Read API proto](https://github.com/googleapis/googleapis/blob/master/google/cloud/bigquery/storage/v1/storage.proto)
documents two distinct Arrow byte fields:

| Field | Documented content |
|---|---|
| ``ReadSession.arrow_schema.serialized_schema`` | IPC-serialized Arrow schema (single ``Schema`` message). |
| ``ArrowRecordBatch.serialized_record_batch`` | IPC-serialized Arrow record batch (single ``RecordBatch`` message). |

bqemulator v1.0.0 packed a **full** Arrow IPC stream
([``schema-message``, ``batch-message``, ``EOS-marker``]) into
``serialized_record_batch``. Real Storage Read clients trip on the
format mismatch: ``google-cloud-bigquery-storage``'s
``reader.to_arrow(session)`` internally calls
``pyarrow.ipc.read_record_batch(bytes, schema)`` which refuses anything
that isn't a bare ``RecordBatch`` message — it raises
``OSError: Expected IPC message of type record batch but got schema``.

## Decision

The producer-side helper
``bqemulator.streaming.read_session.serialize_arrow_record_batch(batch)``
emits **only** the bare ``RecordBatch`` IPC message bytes for the
batch passed in. Schema travels separately on
``ReadSession.arrow_schema.serialized_schema`` (set once per
session) and the first ``ReadRowsResponse.arrow_schema`` (mirrored
for streaming readers that join mid-stream).

### Contract

| Aspect | Behaviour |
|---|---|
| **Return value** | Exactly the IPC bytes for **one** ``RecordBatch`` message (continuation marker + metadata length + flatbuffer metadata + padding + body buffers). No schema-message prefix, no EOS-marker suffix. |
| **Dictionary-encoded fields** | **Rejected at the producer boundary** with ``ValueError``. The wire format has only two slots (schema + batch); pyarrow's ``read_record_batch`` requires a populated ``DictionaryMemo`` to decode dict frames, which a bare-message format can't provide. The check recurses into nested types (``struct``, ``list``, ``map``, ``union``, …) so dict children in any container also fail loudly. |
| **Non-batch IPC messages** | Skipped while walking the transient stream — defensive against future pyarrow message types (compression headers, etc.) that may interleave between the schema and the batch. |
| **Empty input** | A pyarrow stream with no ``RecordBatch`` message raises ``RuntimeError`` (writer-side defect, not a runtime case). |

### Implementation sketch

```python
def serialize_arrow_record_batch(batch: pa.RecordBatch) -> bytes:
    for field in batch.schema:
        if _type_contains_dictionary(field.type):
            raise ValueError(...)
    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, batch.schema)
    writer.write_batch(batch)
    writer.close()
    reader = pa.ipc.MessageReader.open_stream(pa.BufferReader(sink.getvalue()))
    while True:
        msg = reader.read_next_message()
        if msg.type == "record batch":
            return _serialize_one_message_to_bytes(msg)
```

The implementation uses pyarrow's high-level ``new_stream`` writer
then peels off the schema-message prefix and EOS-marker suffix via a
``MessageReader``. This is portable across pyarrow versions; the
low-level ``pa.ipc.write_message`` API has signature drift between
14.x and 17.x+ that the helper avoids.

## Rationale

### Why bare-message, not full-stream

The bare-message format **is** the wire contract real BigQuery
uses. Emitting a full stream made the v1.0.0 emulator
non-interoperable with the canonical
``google-cloud-bigquery-storage`` reader path. Bytes that "work"
with ``pa.ipc.open_stream(payload).read_all()`` (the v1.0.0
implementation's de-facto consumer) are silent compatibility with
nothing — the goal is silent compatibility with the real client.

### Why reject dict-encoded batches, not preserve them

Three options were considered:

1. **Preserve dict frames** by extending the proto with a
   ``dictionary_batches`` field. Out of scope — diverges from real
   BigQuery's wire format; any real client would ignore the extra
   field and fail to decode.
2. **Concatenate** schema-message + dict-messages + batch-message
   into the same ``serialized_record_batch`` payload. Breaks the
   ``ArrowRecordBatch`` proto contract (which carries one record-batch
   message); any conforming consumer that calls
   ``read_record_batch(bytes, schema)`` still fails.
3. **Reject at the producer.** ✅ Real BigQuery doesn't surface
   dict-encoded columns through Storage Read either — Avro for
   low-cardinality, Arrow for the rest, both as plain types.
   Producer-side rejection at the boundary matches the actual wire
   contract. A clear ``ValueError`` with the offending column name
   beats a silent corrupt-payload bug.

### Why recurse into nested types

``pa.types.is_dictionary(t)`` only inspects the top-level type
([pyarrow 14 source](https://arrow.apache.org/docs/14.0/_modules/pyarrow/types.html)).
Arrow's IPC format emits ``DictionaryBatch`` messages for
dict-encoded children inside structs / unions / lists / maps too
([arrow-rs commit 85402148c3af03d](https://github.com/apache/arrow-rs/commit/85402148c3af03d0855e81f855715ea98a7491c5)).
A flat check at the top level would silently allow nested dict
fields and produce the same corrupt-payload bug the rejection was
meant to prevent. The serializer recurses through
``struct``/``list``/``large_list``/``fixed_size_list``/``map``/``union``
children to catch every dict-encoded leaf.

## Consequences

- The canonical
  ``google-cloud-bigquery-storage`` reader path now works against
  bqemulator unchanged — the v1.0.0 pyspark-bigquery example's
  inline ``open_stream`` workaround was dropped in the same commit.
- ``serialize_arrow_ipc(table)`` was removed from ``__all__`` (the
  full-stream helper was the v1.0.0 wrong path, no callers).
- Tables containing dict-encoded columns at any nesting depth
  must be flattened before going through the Storage Read API.
  This is consistent with real BigQuery's behaviour but should be
  documented in the per-language quickstart guides as users
  encounter the ``ValueError``.

## References

- Issue #15 — the consumer-side ``read_record_batch`` failure that
  surfaced the v1.0.0 mismatch.
- [Apache Arrow IPC format](https://arrow.apache.org/docs/format/Columnar.html#serialization-and-interprocess-communication-ipc)
- [PyArrow IPC docs](https://arrow.apache.org/docs/14.0/python/ipc.html)
- [BigQuery Storage Read API proto](https://github.com/googleapis/googleapis/blob/master/google/cloud/bigquery/storage/v1/storage.proto)
- ADR 0030 — sibling decision documenting the Avro alternative
  for the same RPC.
