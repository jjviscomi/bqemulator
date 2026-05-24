#!/usr/bin/env python3
"""Generate the reference Avro OCFs under tests/fixtures/avro/ (ADR 0030).

For each entry in :data:`tests.fixtures.avro._schemas.REFERENCE_SCHEMAS`
this script:

1. Materialises a sample Arrow table matching the entry's schema (the
   exact row content is deterministic per fixture name — see
   ``_sample_rows_for``).
2. Runs the emulator's
   :func:`bqemulator.streaming.avro_serializer.arrow_schema_to_avro_json`
   to derive the Avro schema, then uses :func:`fastavro.writer` to
   emit an OCF on disk.

The committed files act as the canonical "real Avro file" reference
in the integration suite. Re-run after any change to the schema
converter via ``make generate-avro-fixtures``.

Usage::

    python scripts/generate_avro_fixtures.py [--output-dir tests/fixtures/avro/]
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

import fastavro
import pyarrow as pa

# scripts/ → repo root → tests.fixtures.* importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.fixtures.avro._schemas import REFERENCE_SCHEMAS  # noqa: E402

from bqemulator.streaming.avro_serializer import arrow_schema_to_avro_json  # noqa: E402


def _sample_rows_for(name: str) -> list[dict[str, Any]]:
    """Deterministic sample rows per fixture name."""
    if name == "read_session_avro_basic":
        return [
            {"id": 1, "value": "alpha", "category": "a"},
            {"id": 2, "value": "beta", "category": "b"},
            {"id": 3, "value": "gamma", "category": "a"},
        ]
    if name == "read_session_avro_multi_stream":
        return [{"id": i, "payload": f"row_{i}"} for i in range(20)]
    if name == "read_session_avro_all_types":
        return [
            {
                "i": 42,
                "f": 3.14159,
                "s": "hello world",
                "b": True,
                "n": Decimal("123.456789012"),
                "d": date(2026, 5, 20),
                "ts": datetime(2026, 5, 20, 12, 34, 56, tzinfo=UTC),
            }
        ]
    if name == "read_session_avro_nested_struct":
        return [
            {"id": 1, "point": {"x": 10, "y": 20}, "tags": ["a", "b"]},
            {"id": 2, "point": {"x": 30, "y": 40}, "tags": ["c"]},
        ]
    if name == "read_session_avro_with_projection":
        return [
            {"a": 1, "c": "x"},
            {"a": 2, "c": "y"},
            {"a": 3, "c": "z"},
        ]
    if name == "read_session_avro_split_read_stream":
        return [{"id": i, "kind": "even" if i % 2 == 0 else "odd"} for i in range(8)]
    msg = f"unknown reference fixture name: {name!r}"
    raise ValueError(msg)


def _write_one(name: str, schema: pa.Schema, output_dir: Path) -> Path:
    """Emit one reference OCF; return the path written."""
    rows = _sample_rows_for(name)
    table = pa.Table.from_pylist(rows, schema=schema)
    avro_json = arrow_schema_to_avro_json(table.schema)
    parsed = fastavro.parse_schema(json.loads(avro_json))
    output_path = output_dir / f"{name}.avro"
    with output_path.open("wb") as fh:
        fastavro.writer(fh, parsed, rows)
    return output_path


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_REPO_ROOT / "tests" / "fixtures" / "avro",
        help="Where to write the .avro files (default: tests/fixtures/avro/).",
    )
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Generating {len(REFERENCE_SCHEMAS)} reference Avro OCFs into {args.output_dir}")
    for name, schema in REFERENCE_SCHEMAS.items():
        path = _write_one(name, schema, args.output_dir)
        print(f"  wrote {path}")
    print("Done.")
    return 0


if __name__ == "__main__":  # pragma: no cover — script entrypoint
    raise SystemExit(main())
