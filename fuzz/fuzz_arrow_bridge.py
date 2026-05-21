r"""Atheris harness for the Arrow bridge + Arrow IPC deserialiser.

Two surfaces share one harness because the resume-prompt's
``fuzz_arrow_bridge.py`` slot covers both the row-format bridge in
:mod:`bqemulator.storage.arrow_bridge` (BigQuery REST JSON ↔ Arrow) and
the IPC-bytes deserialiser in
:mod:`bqemulator.streaming.arrow_deserializer` (raw Arrow IPC wire
bytes → :class:`pyarrow.Table`). Both are translator-input boundaries
that consume client-controlled payloads.

Contract:

* :class:`ValueError` and :class:`pyarrow.ArrowInvalid` (the
  documented error class
  :func:`bqemulator.streaming.arrow_deserializer.deserialize_arrow_rows`
  surfaces on a malformed IPC payload) are caught — they are the
  expected error envelopes. Any other exception escaping either entry
  point is a bug.
* :class:`bqemulator.domain.errors.DomainError` is caught for the same
  forward-compat reason as in the sibling translator harness.

The harness splits each fuzzer-provided ``bytes`` blob between the two
surfaces so a single libFuzzer invocation exercises both: the first
slice drives :func:`deserialize_arrow_rows`; the second slice drives
:func:`bq_rows_to_arrow` after being parsed into a row-shape
JSON-equivalent dict.

Run directly::

    python fuzz/fuzz_arrow_bridge.py -max_total_time=60 \
        fuzz/corpus/arrow_bridge
"""

from __future__ import annotations

import sys
from typing import Any

import atheris

with atheris.instrument_imports():
    from bqemulator.storage.arrow_bridge import bq_rows_to_arrow
    from bqemulator.streaming.arrow_deserializer import deserialize_arrow_rows

import pyarrow as pa

from bqemulator.domain.errors import DomainError

# A fixed target schema for ``bq_rows_to_arrow``. One column of each
# major BigQuery type so the type-dispatch branches in
# :func:`bqemulator.storage.arrow_bridge._coerce_to_arrow_value` are
# all reachable from a single fuzz iteration.
_TARGET_SCHEMA = pa.schema(
    [
        pa.field("i", pa.int64()),
        pa.field("f", pa.float64()),
        pa.field("s", pa.string()),
        pa.field("b", pa.bool_()),
        pa.field("by", pa.binary()),
        pa.field("d", pa.date32()),
        pa.field("ts", pa.timestamp("us", tz="UTC")),
        pa.field("l", pa.list_(pa.int64())),
    ],
)


def _fuzzed_row(fdp: atheris.FuzzedDataProvider) -> dict[str, Any]:
    """Build one row of REST-JSON-shaped values for the row-bridge surface.

    :func:`_coerce_to_arrow_value` is documented to accept BigQuery's
    string-typed REST values; the fuzzer fills each slot with arbitrary
    content so malformed inputs surface.
    """
    return {
        "i": fdp.ConsumeUnicode(64),
        "f": fdp.ConsumeUnicode(64),
        "s": fdp.ConsumeUnicode(256),
        "b": fdp.ConsumeUnicode(8),
        "by": fdp.ConsumeUnicode(128),
        "d": fdp.ConsumeUnicode(32),
        "ts": fdp.ConsumeUnicode(64),
        "l": [fdp.ConsumeUnicode(16) for _ in range(fdp.ConsumeIntInRange(0, 4))],
    }


def TestOneInput(data: bytes) -> None:  # noqa: N802 — libFuzzer entry-point name
    """LibFuzzer entry point — exercise both Arrow surfaces."""
    fdp = atheris.FuzzedDataProvider(data)

    # Surface 1: Arrow IPC deserialiser. The schema half + the batch
    # half are sourced from the fuzzer; either being empty exercises
    # the documented zero-row path.
    schema_len = fdp.ConsumeIntInRange(0, 1024)
    schema_bytes = fdp.ConsumeBytes(schema_len)
    batch_bytes = fdp.ConsumeBytes(1024)
    try:
        deserialize_arrow_rows(schema_bytes, batch_bytes)
    except (ValueError, pa.ArrowInvalid):
        # Documented contract — see module docstring.
        pass
    except DomainError:
        pass

    # Surface 2: REST-JSON-shaped row → Arrow table. A few fuzzed
    # rows per iteration so the coercion branches see multiple
    # values per call.
    row_count = fdp.ConsumeIntInRange(0, 4)
    rows = [{"json": _fuzzed_row(fdp)} for _ in range(row_count)]
    try:
        bq_rows_to_arrow(rows, _TARGET_SCHEMA)
    except (ValueError, pa.ArrowInvalid):
        pass
    except DomainError:
        pass


def _main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    _main()
