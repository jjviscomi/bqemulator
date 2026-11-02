"""Unit coverage for the declarative builtin-UDF registry.

:data:`bqemulator.sql.builtin_udfs.BUILTIN_UDFS` is the one source of
each helper's DuckDB signature: :func:`register_builtin_udfs` registers
it on the engine's connection, and the translator's type annotation
reads its return types.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.sql.builtin_udfs import BUILTIN_UDFS, register_builtin_udfs

pytestmark = pytest.mark.unit


def test_every_helper_is_registered_with_its_signature() -> None:
    connection = duckdb.connect()
    register_builtin_udfs(connection)
    registered = connection.execute(
        "SELECT function_name, parameter_types, return_type FROM duckdb_functions() "
        "WHERE function_name LIKE 'bqemu_%' ORDER BY function_name",
    ).fetchall()
    declared = sorted(
        (
            udf.name,
            [_duckdb_type_name(t) for t in udf.parameter_types],
            _duckdb_type_name(udf.return_type),
        )
        for udf in BUILTIN_UDFS
    )
    assert registered == declared


def _duckdb_type_name(type_sql: str) -> str:
    """Return DuckDB's canonical spelling of *type_sql* (``DECIMAL(38,10)``)."""
    return str(duckdb.sqltype(type_sql))


def test_helper_names_are_unique() -> None:
    names = [udf.name for udf in BUILTIN_UDFS]
    assert len(names) == len(set(names))
