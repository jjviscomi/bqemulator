"""Typed identifiers for BigQuery resources.

BigQuery's identifier rules (from the docs):

* ``project_id``: 6-30 characters; lowercase letters, digits, hyphens; must
  start with a letter and not end with a hyphen.
* ``dataset_id``: up to 1024 characters; letters (any case), digits,
  underscores. No hyphens or dots.
* ``table_id``: up to 1024 characters; same character set as dataset, plus
  hyphens and some Unicode letters in certain contexts.
* ``job_id``: up to 1024 characters; letters, digits, dashes, underscores.
* ``routine_id``: same as dataset.

We model each as a ``frozen`` dataclass with a validating constructor.
The raw string is exposed via the ``value`` attribute; equality and hashing
work as expected.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from bqemulator.domain.errors import ValidationError

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_TABLE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,1024}$")
_JOB_RE = re.compile(r"^[A-Za-z0-9_\-]{1,1024}$")
_ROUTINE_RE = _DATASET_RE


def _validate(pattern: re.Pattern[str], value: str, kind: str) -> None:
    if not pattern.match(value):
        raise ValidationError(f"Invalid {kind} id: {value!r}")


@dataclass(slots=True, frozen=True)
class ProjectId:
    """A validated BigQuery project id."""

    value: str

    def __post_init__(self) -> None:
        _validate(_PROJECT_RE, self.value, "project")

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True, frozen=True)
class DatasetId:
    """A validated BigQuery dataset id (without project qualification)."""

    value: str

    def __post_init__(self) -> None:
        _validate(_DATASET_RE, self.value, "dataset")

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True, frozen=True)
class TableId:
    """A validated BigQuery table id (without dataset qualification)."""

    value: str

    def __post_init__(self) -> None:
        _validate(_TABLE_RE, self.value, "table")

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True, frozen=True)
class JobId:
    """A validated BigQuery job id."""

    value: str

    def __post_init__(self) -> None:
        _validate(_JOB_RE, self.value, "job")

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True, frozen=True)
class RoutineId:
    """A validated BigQuery routine id (without dataset qualification)."""

    value: str

    def __post_init__(self) -> None:
        _validate(_ROUTINE_RE, self.value, "routine")

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# SQL-boundary validators
#
# Any code that interpolates a project/dataset/table id into a DuckDB SQL
# string must call these first. They raise :class:`ValidationError` for
# anything that doesn't match BigQuery's identifier rules, which also
# makes them an airtight defense against SQL injection (no quotes,
# semicolons, comments, or whitespace can slip through).
# ---------------------------------------------------------------------------


def validate_project_id(value: str) -> str:
    """Return ``value`` unchanged if it is a valid project id; else raise."""
    _validate(_PROJECT_RE, value, "project")
    return value


def validate_dataset_id(value: str) -> str:
    """Return ``value`` unchanged if it is a valid dataset id; else raise."""
    _validate(_DATASET_RE, value, "dataset")
    return value


def validate_table_id(value: str) -> str:
    """Return ``value`` unchanged if it is a valid table id; else raise."""
    _validate(_TABLE_RE, value, "table")
    return value


def validate_routine_id(value: str) -> str:
    """Return ``value`` unchanged if it is a valid routine id; else raise."""
    _validate(_ROUTINE_RE, value, "routine")
    return value


def validate_job_id(value: str) -> str:
    """Return ``value`` unchanged if it is a valid job id; else raise."""
    _validate(_JOB_RE, value, "job")
    return value


def validate_table_ref(
    project_id: str,
    dataset_id: str,
    table_id: str,
) -> tuple[str, str, str]:
    """Validate a (project, dataset, table) triple in one call."""
    return (
        validate_project_id(project_id),
        validate_dataset_id(dataset_id),
        validate_table_id(table_id),
    )


__all__ = [
    "DatasetId",
    "JobId",
    "ProjectId",
    "RoutineId",
    "TableId",
    "validate_dataset_id",
    "validate_job_id",
    "validate_project_id",
    "validate_routine_id",
    "validate_table_id",
    "validate_table_ref",
]
