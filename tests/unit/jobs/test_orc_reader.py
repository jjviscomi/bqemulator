"""Unit tests for the ORC → Arrow bridge (G1, ADR 0027).

Covers the deeper type-mapping branches in ``_orc_type_to_arrow``
that the integration tests don't reach (timestamp, date, decimal,
varchar, array) plus the missing-``pyorc`` UnsupportedFeatureError
path so the optional-extra contract stays locked in.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pyarrow as pa
import pyorc
import pytest

from bqemulator.domain.errors import UnsupportedFeatureError
from bqemulator.jobs.orc_reader import (
    _orc_schema_to_arrow,
    _orc_type_to_arrow,
    read_orc_to_arrow,
)

pytestmark = pytest.mark.unit


def _write_orc(path: Path, schema_str: str, rows: list[tuple]) -> None:
    with path.open("wb") as fh:
        writer = pyorc.Writer(fh, schema_str)
        for row in rows:
            writer.write(row)
        writer.close()


class TestOrcTypeToArrow:
    """Per-kind ORC TypeDescription → pyarrow DataType mapping."""

    @pytest.mark.parametrize(
        ("orc_schema", "expected_field_type"),
        [
            ("struct<x:boolean>", pa.bool_()),
            ("struct<x:tinyint>", pa.int8()),
            ("struct<x:smallint>", pa.int16()),
            ("struct<x:int>", pa.int32()),
            ("struct<x:bigint>", pa.int64()),
            ("struct<x:float>", pa.float32()),
            ("struct<x:double>", pa.float64()),
            ("struct<x:string>", pa.string()),
            ("struct<x:binary>", pa.binary()),
            ("struct<x:date>", pa.date32()),
            ("struct<x:timestamp>", pa.timestamp("us", tz="UTC")),
        ],
    )
    def test_primitive_round_trip(
        self,
        orc_schema: str,
        expected_field_type: pa.DataType,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "p.orc"
        # Some primitives need a real value to write; for date / timestamp we
        # write None to keep the row construction simple.
        import datetime as dt

        sample = {
            pa.bool_(): True,
            pa.int8(): 1,
            pa.int16(): 1,
            pa.int32(): 1,
            pa.int64(): 1,
            pa.float32(): 1.0,
            pa.float64(): 1.0,
            pa.string(): "a",
            pa.binary(): b"a",
            pa.date32(): dt.date(2026, 5, 20),
            pa.timestamp("us", tz="UTC"): dt.datetime(2026, 5, 20, tzinfo=dt.UTC),
        }
        _write_orc(path, orc_schema, [(sample[expected_field_type],)])

        table = read_orc_to_arrow(str(path))
        assert table.schema.field(0).type == expected_field_type
        assert table.num_rows == 1

    def test_varchar_maps_to_string(self, tmp_path: Path) -> None:
        path = tmp_path / "v.orc"
        _write_orc(path, "struct<x:varchar(10)>", [("hi",)])
        table = read_orc_to_arrow(str(path))
        assert table.schema.field(0).type == pa.string()

    def test_char_maps_to_string(self, tmp_path: Path) -> None:
        path = tmp_path / "c.orc"
        # pyorc pads char(N) values with spaces, but the type maps to string.
        _write_orc(path, "struct<x:char(3)>", [("hi",)])
        table = read_orc_to_arrow(str(path))
        assert table.schema.field(0).type == pa.string()

    def test_decimal_preserves_precision_scale(self, tmp_path: Path) -> None:
        import decimal

        path = tmp_path / "d.orc"
        _write_orc(
            path,
            "struct<x:decimal(18,4)>",
            [(decimal.Decimal("1.2345"),)],
        )
        table = read_orc_to_arrow(str(path))
        decimal_type = table.schema.field(0).type
        assert pa.types.is_decimal(decimal_type)
        assert decimal_type.precision == 18
        assert decimal_type.scale == 4

    def test_array_maps_to_list(self, tmp_path: Path) -> None:
        path = tmp_path / "a.orc"
        _write_orc(path, "struct<xs:array<int>>", [([1, 2, 3],)])
        table = read_orc_to_arrow(str(path))
        arr_type = table.schema.field(0).type
        assert pa.types.is_list(arr_type)
        assert arr_type.value_type == pa.int32()

    def test_unknown_kind_falls_back_to_string(self) -> None:
        """Defensive fallback: unknown ORC kinds map to string, not crash."""

        class _FakeType:
            def __str__(self) -> str:
                return "uniontype<int,string>"

        # The fallback branch is the last line in _orc_type_to_arrow.
        result = _orc_type_to_arrow(_FakeType())
        assert result == pa.string()


class TestOrcSchemaToArrow:
    def test_top_level_struct_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "t.orc"
        _write_orc(
            path,
            "struct<id:bigint,name:string>",
            [(1, "a"), (2, "b")],
        )
        with path.open("rb") as fh:
            schema = pyorc.Reader(fh).schema
        arrow_schema = _orc_schema_to_arrow(schema)
        assert arrow_schema.names == ["id", "name"]
        assert arrow_schema.field("id").type == pa.int64()
        assert arrow_schema.field("name").type == pa.string()


class TestReadOrcMissingPyorc:
    """The optional-extra contract: clear UnsupportedFeatureError if pyorc absent."""

    def test_unsupported_when_pyorc_import_fails(self, tmp_path: Path) -> None:
        # Simulate pyorc being absent at import time.
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "pyorc":
                raise ImportError("simulated: pyorc missing")
            return real_import(name, *args, **kwargs)

        # We need to patch builtins.__import__ so the lazy import inside
        # read_orc_to_arrow fails the way it would on a real install
        # without the [orc] extra.
        with (
            mock.patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(UnsupportedFeatureError, match=r"orc.*extra"),
        ):
            read_orc_to_arrow(str(tmp_path / "anything.orc"))
