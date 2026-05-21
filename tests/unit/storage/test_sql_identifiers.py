"""Unit tests for :mod:`bqemulator.storage.sql_identifiers`.

The SQL-boundary helpers are the only place that guards our string-built
INSERT / CREATE / SELECT statements against injection. These tests pin
the whitelist so a future loosening would fail CI.
"""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import ValidationError
from bqemulator.storage.sql_identifiers import (
    quoted_schema,
    quoted_table_ref,
    register_name,
    schema_name,
)

pytestmark = pytest.mark.unit


class TestValidIdentifiers:
    @pytest.mark.parametrize(
        "project_id",
        ["p", "proj", "my-project", "test_project", "ABC123", "A1B2C3D4E5"],
    )
    def test_valid_project_ids(self, project_id: str) -> None:
        """Real-world test project ids are all accepted."""
        assert quoted_schema(project_id, "ds") == f'"{project_id}__ds"'

    @pytest.mark.parametrize(
        "table_id",
        [
            "orders",
            "events_20260401",
            "Users",
            "my-table",
            "table_with_underscores",
            "A" * 255,  # longest-allowed id
        ],
    )
    def test_valid_table_ids(self, table_id: str) -> None:
        """Long and short table ids round-trip through the quoter."""
        assert quoted_table_ref("proj", "ds", table_id) == f'"proj__ds"."{table_id}"'


class TestSqlInjectionDefense:
    """Every payload below must raise before reaching SQL."""

    @pytest.mark.parametrize(
        "malicious",
        [
            'p"; DROP TABLE users --',
            "p' OR 1=1 --",
            "p; DROP SCHEMA foo",
            "p\ndrop table",
            "p\x00null",
            "p\tinjected",
            "p$injection",
            "p/*comment*/",
            "p%wildcard",
            "",  # empty
            "A" * 256,  # too long
        ],
    )
    def test_project_id_rejected(self, malicious: str) -> None:
        """Project ids containing SQL-dangerous chars raise."""
        with pytest.raises(ValidationError):
            schema_name(malicious, "ds")

    @pytest.mark.parametrize(
        "malicious",
        [
            'ds"); DROP TABLE x; --',
            "ds;DROP",
            "ds' UNION SELECT ",
            "ds)--",
        ],
    )
    def test_dataset_id_rejected(self, malicious: str) -> None:
        with pytest.raises(ValidationError):
            schema_name("proj", malicious)

    @pytest.mark.parametrize(
        "malicious",
        [
            't"; DROP TABLE users --',
            "t UNION SELECT password",
            "t/*comment*/",
            "t|other",
        ],
    )
    def test_table_id_rejected(self, malicious: str) -> None:
        with pytest.raises(ValidationError):
            quoted_table_ref("proj", "ds", malicious)


class TestRegisterName:
    def test_valid_register_name_roundtrips(self) -> None:
        """Generated register names pass validation."""
        assert register_name("__bqemu_write_abc123") == "__bqemu_write_abc123"

    @pytest.mark.parametrize(
        "bad",
        [
            "not_prefixed",
            "__bqemu_",  # empty suffix
            "__bqemu_with space",
            "__bqemu_" + "x" * 70,  # too long
            "__bqemu_bad;drop",
            '__bqemu_"quote',
        ],
    )
    def test_invalid_register_name_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            register_name(bad)
