"""SQL-identifier helpers for the DuckDB storage layer.

All code that interpolates a BigQuery project / dataset / table id into a
DuckDB SQL string MUST go through these helpers instead of building the
string by hand. They apply the SQL-boundary whitelist (more permissive
than the resource-modelling rules in :mod:`bqemulator.domain.ids`
because real BigQuery users test with short non-compliant project ids
like ``test-project`` or ``proj``) and wrap the result in DuckDB's
double-quote identifier syntax.

Rules: every id must match ``[A-Za-z0-9_][A-Za-z0-9_-]{0,254}``. That
rejects every SQL-dangerous byte — quotes, semicolons, whitespace,
parentheses, comments, zero bytes — while keeping real-world test ids
valid. It is strictly narrower than
:func:`bqemulator.domain.ids.validate_table_id`, which is used for
resource-id parsing at the REST/gRPC boundary.

Use :func:`schema_name` / :func:`quoted_schema` to build the
``{project}__{dataset}`` DuckDB schema reference, and
:func:`quoted_table_ref` to build ``"schema"."table"`` for FROM / INSERT.

``register_name`` exists to sanity-check names passed to
``DuckDBPyConnection.register`` — we generate those ourselves with
``uuid4().hex``, but the helper keeps the guarantee uniform.
"""

from __future__ import annotations

import re

from bqemulator.domain.errors import ValidationError

_REGISTER_NAME_RE = re.compile(r"^__bqemu_[A-Za-z0-9_]{1,64}$")

# SQL-boundary whitelist: start with alnum or underscore, then any of
# alnum/underscore/hyphen, up to 255 chars. Narrower than DuckDB's own
# identifier grammar, wider than BigQuery's project-id rule.
_SQL_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,254}$")


def _validate_sql_id(value: str, kind: str) -> str:
    """Reject any identifier containing SQL-dangerous characters."""
    if not isinstance(value, str) or not _SQL_SAFE_ID_RE.match(value):
        raise ValidationError(f"Invalid {kind} id for SQL: {value!r}")
    return value


def schema_name(project_id: str, dataset_id: str) -> str:
    """Return the DuckDB schema name ``{project}__{dataset}`` (unquoted).

    Raises :class:`ValidationError` if either id is malformed.
    """
    return f"{_validate_sql_id(project_id, 'project')}__{_validate_sql_id(dataset_id, 'dataset')}"


def quoted_schema(project_id: str, dataset_id: str) -> str:
    """Return the DuckDB schema reference, double-quoted."""
    return f'"{schema_name(project_id, dataset_id)}"'


def quoted_table_ref(project_id: str, dataset_id: str, table_id: str) -> str:
    """Return the fully-quoted ``"{schema}"."{table}"`` reference."""
    validated_table = _validate_sql_id(table_id, "table")
    return f'{quoted_schema(project_id, dataset_id)}."{validated_table}"'


def register_name(name: str) -> str:
    """Validate a DuckDB ``register()`` name and return it unchanged.

    Register names are generated in-process (e.g. ``__bqemu_write_<uuid>``)
    so the validator just provides defense-in-depth.
    """
    if not _REGISTER_NAME_RE.match(name):
        raise ValidationError(f"Invalid DuckDB register name: {name!r}")
    return name


__all__ = [
    "quoted_schema",
    "quoted_table_ref",
    "register_name",
    "schema_name",
]
