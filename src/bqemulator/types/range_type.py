"""``RANGE<T>`` type — backed by ``STRUCT<start T, "end" T>`` in DuckDB.

DuckDB has no native RANGE type, so we model BigQuery's RANGE as a
two-field struct. ``start`` and ``end`` are the field names BigQuery
exposes; ``end`` is a SQL keyword in many dialects (DuckDB tolerates
it unquoted in field-access positions, but identifiers in DDL must be
quoted). This module owns the helpers that map between the two
representations and validate the element type.

The element type set is locked by BigQuery: ``DATE``, ``DATETIME``,
``TIMESTAMP``. Any other element type is a parse-time error. ADR 0019
records the design.
"""

from __future__ import annotations

import re
from typing import Final

from bqemulator.domain.errors import ValidationError

#: Valid BigQuery RANGE element types.
VALID_ELEMENT_TYPES: Final[tuple[str, ...]] = ("DATE", "DATETIME", "TIMESTAMP")

#: Field names used inside the backing DuckDB STRUCT. ``end`` matches
#: BigQuery's projection name (``r.end``); we quote it in DDL because
#: DuckDB requires the quote in CREATE-TABLE positions.
START_FIELD: Final[str] = "start"
END_FIELD: Final[str] = "end"


def validate_element_type(element_type: str) -> str:
    """Validate and uppercase a RANGE element type.

    Args:
        element_type: BigQuery type name supplied by the user.

    Returns:
        The uppercase canonical form.

    Raises:
        ValidationError: If the element type is not one of
            :data:`VALID_ELEMENT_TYPES`.
    """
    upper = element_type.strip().upper()
    if upper not in VALID_ELEMENT_TYPES:
        raise ValidationError(
            f"RANGE element type must be one of "
            f"{', '.join(VALID_ELEMENT_TYPES)} (got {element_type!r})",
        )
    return upper


def duckdb_struct_for(element_type: str) -> str:
    """Return the DuckDB ``STRUCT(...)`` type spec for a RANGE element type.

    Example: ``duckdb_struct_for("DATE")`` →
    ``STRUCT("start" DATE, "end" DATE)``.

    The fields are double-quoted to keep ``end`` valid in CREATE TABLE
    DDL (DuckDB treats unquoted ``end`` as a keyword in that
    position).
    """
    canon = validate_element_type(element_type)
    duck_inner = _bq_to_duckdb_element(canon)
    return f'STRUCT("{START_FIELD}" {duck_inner}, "{END_FIELD}" {duck_inner})'


def _bq_to_duckdb_element(bq_type: str) -> str:
    """Map a validated BigQuery range element to its DuckDB type."""
    # Local mapping to avoid circular import with storage.type_map.
    return {
        "DATE": "DATE",
        "DATETIME": "TIMESTAMP",  # BigQuery DATETIME is naive; DuckDB TIMESTAMP is naive too.
        "TIMESTAMP": "TIMESTAMPTZ",
    }[bq_type]


def parse_bq_range_type(bq_type: str) -> str:
    """Parse a BigQuery ``RANGE<T>`` type string and return the element type.

    Accepts ``RANGE<DATE>``, ``RANGE<DATETIME>``, ``RANGE<TIMESTAMP>``
    (case-insensitive, whitespace tolerant). Raises
    :class:`ValidationError` for any other shape.
    """
    upper = bq_type.strip().upper()
    if not upper.startswith("RANGE<") or not upper.endswith(">"):
        raise ValidationError(
            f"Expected RANGE<DATE|DATETIME|TIMESTAMP>, got {bq_type!r}",
        )
    inner = bq_type.strip()[6:-1].strip()
    return validate_element_type(inner)


#: Matches the canonical DuckDB type string for the ``STRUCT("start" T,
#: "end" T)`` shape ``RANGE<T>`` materialises as. Optional trailing
#: ``[]`` indicates a list-of-RANGE (REPEATED mode on the BigQuery
#: wire). The inner element type captures one of the three DuckDB
#: equivalents — ``DATE``, naive ``TIMESTAMP``, or ``TIMESTAMP WITH
#: TIME ZONE`` — used by :func:`detect_range_element` to recover the
#: BigQuery element name.
_DUCKDB_RANGE_TYPE_RE: Final = re.compile(
    r'^\s*STRUCT\(\s*"start"\s+(?P<elem>DATE|TIMESTAMP(?:\s+WITH\s+TIME\s+ZONE)?)'
    r'\s*,\s*"end"\s+(?P=elem)\s*\)(?P<repeated>\[\])?\s*$',
    re.IGNORECASE,
)

#: Mapping from a captured DuckDB element-type string (normalised to
#: upper-case + collapsed whitespace) to the BigQuery RANGE element
#: name on the wire. ADR 0019 §2 pins these to BigQuery's three
#: supported RANGE elements.
_DUCKDB_ELEM_TO_BQ: Final[dict[str, str]] = {
    "DATE": "DATE",
    "TIMESTAMP": "DATETIME",  # naive DuckDB TIMESTAMP ⇄ BigQuery DATETIME
    "TIMESTAMP WITH TIME ZONE": "TIMESTAMP",
}


def detect_range_element(duckdb_type: str | None) -> tuple[str, bool] | None:
    """Detect whether a DuckDB column type represents a BigQuery RANGE.

    Returns ``(bq_element_type, is_repeated)`` on a positive match — the
    element type is one of :data:`VALID_ELEMENT_TYPES` and
    ``is_repeated`` is True iff the DuckDB type was a list of the
    RANGE-shaped struct (the BigQuery REPEATED-mode wire shape).
    Returns ``None`` for any other DuckDB type, including unrelated
    structs that happen to have ``start`` / ``end`` fields with
    incompatible inner types.

    The helper is the single source of truth for both the wire-format
    schema renderer in :mod:`bqemulator.jobs.executor` and the row
    encoder in :mod:`bqemulator.storage.arrow_bridge`. Centralising the
    pattern here ensures a future format change (DuckDB renaming
    ``TIMESTAMP WITH TIME ZONE`` to ``TIMESTAMPTZ`` in column type
    strings, for example) touches one site.
    """
    if not duckdb_type:
        return None
    match = _DUCKDB_RANGE_TYPE_RE.match(duckdb_type)
    if match is None:
        return None
    elem_raw = " ".join(match.group("elem").upper().split())
    bq_elem = _DUCKDB_ELEM_TO_BQ.get(elem_raw)
    if bq_elem is None:
        return None
    return (bq_elem, match.group("repeated") is not None)


__all__ = [
    "END_FIELD",
    "START_FIELD",
    "VALID_ELEMENT_TYPES",
    "detect_range_element",
    "duckdb_struct_for",
    "parse_bq_range_type",
    "validate_element_type",
]
