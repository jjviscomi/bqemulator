"""Stage canonical Avro/ORC test files under a GCS-local-root dir.

Used by both the Python testcontainer conftest (via direct import)
and the Node/Go/Java Makefile recipes (via subprocess invocation
before ``docker run``). Centralising the file generation keeps every
language E2E exercising the *same* bytes.

Output layout under ``<root>``:

    g1-e2e/
      load_avro_basic.avro    — 3 rows {id:int, name:string?}
      load_orc_basic.orc      — 3 rows {id:bigint, name:string}
"""

from __future__ import annotations

import argparse
from pathlib import Path
import stat
import sys


def stage(root: Path) -> None:
    """Write the canonical Avro + ORC fixtures under ``root``.

    Files are written world-readable so the non-root ``bqemu`` user
    (UID 1000) inside the container can read them when the host user
    is different (typical on macOS Docker Desktop and CI).
    """
    bucket = root / "g1-e2e"
    bucket.mkdir(parents=True, exist_ok=True)
    bucket.chmod(0o777)

    import fastavro
    import pyorc

    # --- load_avro_basic.avro ---
    avro_schema = fastavro.parse_schema(
        {
            "type": "record",
            "name": "Item",
            "fields": [
                {"name": "id", "type": "long"},
                {"name": "name", "type": ["null", "string"], "default": None},
            ],
        },
    )
    avro_records = [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "beta"},
        {"id": 3, "name": "gamma"},
    ]
    avro_path = bucket / "load_avro_basic.avro"
    with avro_path.open("wb") as fh:
        fastavro.writer(fh, avro_schema, avro_records)
    avro_path.chmod(0o644 | stat.S_IROTH)

    # --- load_orc_basic.orc ---
    orc_path = bucket / "load_orc_basic.orc"
    with orc_path.open("wb") as fh:
        writer = pyorc.Writer(fh, "struct<id:bigint,name:string>")
        for row in [(1, "alpha"), (2, "beta"), (3, "gamma")]:
            writer.write(row)
        writer.close()
    orc_path.chmod(0o644 | stat.S_IROTH)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Host directory mounted into the container as BQEMU_GCS_LOCAL_ROOT",
    )
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        args.root.mkdir(parents=True, exist_ok=True)
    stage(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
