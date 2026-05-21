"""ORC file → Arrow table bridge for the load executor (G1).

DuckDB 1.5 does not yet ship a stable ``read_orc`` table function for
all platforms (the community ``orc`` extension 404s on darwin-arm64).
We therefore route ORC reads through the optional ``pyorc`` package
(installed via the ``[orc]`` extra). Each ORC file is read in full
into a :class:`pyarrow.Table` whose schema mirrors the ORC schema,
then inserted into the target table via DuckDB's ``arrow_scan`` table
function.

ORC tends to be used in batch-load workflows (Hadoop/Hive → BigQuery
migrations) where files are bounded in size, so reading the whole file
into memory is acceptable for the emulator's local-developer use case.
ORC *write* is intentionally NOT supported: BigQuery itself does not
extract to ORC, and a full ORC writer is a multi-day scope item — see
``docs/reference/out-of-scope.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from bqemulator.domain.errors import InvalidQueryError, UnsupportedFeatureError

if TYPE_CHECKING:
    import pyarrow as pa


_ORC_TO_ARROW_PRIMITIVE: dict[str, str] = {
    # ``pa.bool_`` (with trailing underscore) — ``pa.bool`` was renamed
    # upstream to avoid shadowing the Python builtin.
    "boolean": "bool_",
    "tinyint": "int8",
    "smallint": "int16",
    "int": "int32",
    "bigint": "int64",
    "float": "float32",
    "double": "float64",
    "string": "string",
    "binary": "binary",
    "date": "date32",
    "timestamp": "timestamp",
}


def read_orc_to_arrow(path: str) -> pa.Table:
    """Read an ORC file into a :class:`pyarrow.Table`.

    Raises:
        UnsupportedFeatureError: if ``pyorc`` is not installed.
        InvalidQueryError: if the file is missing or corrupt.
    """
    try:
        import pyorc
    except ImportError as exc:
        raise UnsupportedFeatureError(
            "Load from ORC requires the optional ``pyorc`` dependency. "
            "Install bqemulator with the ``[orc]`` extra "
            "(``pip install 'bqemulator[orc]'``) or with ``[all]`` to "
            "enable ORC load support.",
        ) from exc

    import pyarrow as pa

    try:
        with open(path, "rb") as fh:  # noqa: PTH123 — pyorc needs a binary stream
            reader = pyorc.Reader(fh, struct_repr=pyorc.StructRepr.DICT)
            schema = reader.schema
            rows = list(reader)
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise InvalidQueryError(f"ORC file not found: {path}") from exc
    except Exception as exc:
        raise InvalidQueryError(f"Failed to read ORC file {path}: {exc}") from exc

    arrow_schema = _orc_schema_to_arrow(schema)
    column_arrays: dict[str, list[Any]] = {field.name: [] for field in arrow_schema}
    for row in rows:
        # pyorc with StructRepr.DICT yields a dict per row, but the library
        # ships no type stubs so mypy infers ``object``. ``cast`` is the
        # cheapest way to express the runtime contract without leaking
        # Any-tainted return types into the public read_orc_to_arrow shape.
        row_dict = cast("dict[str, Any]", row)
        for field in arrow_schema:
            column_arrays[field.name].append(row_dict.get(field.name))
    return pa.table(
        {
            field.name: pa.array(column_arrays[field.name], type=field.type)
            for field in arrow_schema
        },
        schema=arrow_schema,
    )


def _orc_schema_to_arrow(orc_schema: Any) -> pa.Schema:
    """Convert a top-level struct ORC schema to a pyarrow Schema.

    ORC's top-level type is always ``struct<...>``; field types are
    accessible via ``orc_schema.fields`` (an ordered dict mapping field
    name → child :class:`pyorc.TypeDescription`).
    """
    import pyarrow as pa

    fields: list[pa.Field] = []
    for name, child in orc_schema.fields.items():
        fields.append(pa.field(name, _orc_type_to_arrow(child)))
    return pa.schema(fields)


def _orc_type_to_arrow(orc_type: Any) -> pa.DataType:
    """Convert a single ORC type descriptor to an Arrow type."""
    import pyarrow as pa

    kind = str(orc_type).split("(", 1)[0].split("<", 1)[0].strip().lower()
    primitive = _ORC_TO_ARROW_PRIMITIVE.get(kind)
    if primitive == "timestamp":
        return pa.timestamp("us", tz="UTC")
    if primitive == "date32":
        return pa.date32()
    if primitive is not None:
        return getattr(pa, primitive)()
    if kind in {"char", "varchar"}:
        return pa.string()
    if kind == "decimal":
        precision = getattr(orc_type, "precision", 38)
        scale = getattr(orc_type, "scale", 9)
        return pa.decimal128(precision, scale)
    if kind == "struct":
        sub_fields = [
            pa.field(name, _orc_type_to_arrow(child)) for name, child in orc_type.fields.items()
        ]
        return pa.struct(sub_fields)
    if kind in {"array", "list"}:
        elem = (
            next(iter(orc_type.fields.values())) if hasattr(orc_type, "fields") else orc_type.type
        )
        return pa.list_(_orc_type_to_arrow(elem))
    # Fallback — emit as string so the load doesn't silently drop data.
    return pa.string()


__all__ = ["read_orc_to_arrow"]
