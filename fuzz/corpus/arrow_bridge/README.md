# Arrow-bridge fuzz seed corpus

Seed inputs for [`fuzz_arrow_bridge.py`](../../fuzz_arrow_bridge.py).
The harness ingests each blob via Atheris's
:class:`atheris.FuzzedDataProvider`, splitting it between the IPC
deserialiser surface
(:func:`bqemulator.streaming.arrow_deserializer.deserialize_arrow_rows`)
and the row-format bridge surface
(:func:`bqemulator.storage.arrow_bridge.bq_rows_to_arrow`). Atheris's
coverage-guided mutation expands from these.

Invoke with the directory path as a positional argument:

```
python fuzz/fuzz_arrow_bridge.py -max_total_time=60 \
    fuzz/corpus/arrow_bridge
```

Seeds intentionally cover:

* `empty.bin` — zero-byte payload (exercises the
  zero-length-schema branch of `deserialize_arrow_rows`).
* `schema_only.bin` — a valid Arrow IPC stream containing only the
  schema (exercises the zero-row path that returns an empty table).
* `valid_ipc_stream.bin` — a complete schema + record-batch IPC
  stream (exercises the happy-path
  ``pa.ipc.open_stream(...).read_all()`` branch).
* `garbage.bin` — pseudo-Arrow bytes with a leading magic followed
  by junk (exercises the `ArrowInvalid` → `ValueError` mapping path).
