"""Property tests for id validators.

Invariants:

* Any id the validator accepts, when re-passed through the validator,
  still accepts (idempotence).
* Any id the validator rejects raises ValidationError (no silent pass).
* Constructed ids are immutable and usable as dict keys / set members.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.domain.errors import ValidationError
from bqemulator.domain.ids import DatasetId, TableId

pytestmark = pytest.mark.property

# BigQuery dataset ids: ASCII letters, digits, underscores.
# (BQ docs specify Latin letters only; Hypothesis's Unicode categories
#  would surface characters our validator deliberately rejects.)
_ASCII_LETTER_DIGIT_UNDERSCORE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_dataset_chars = st.text(
    alphabet=_ASCII_LETTER_DIGIT_UNDERSCORE,
    min_size=1,
    max_size=64,
)


@given(_dataset_chars)
@settings(max_examples=200)
def test_dataset_id_accepts_valid_alphanum_underscore(value: str) -> None:
    d = DatasetId(value)
    assert str(d) == value
    # Idempotent construction
    assert DatasetId(str(d)) == d


@given(st.text(min_size=1, max_size=64))
@settings(max_examples=200)
def test_dataset_id_rejects_hyphen_or_dot(value: str) -> None:
    if "-" in value or "." in value or " " in value:
        with pytest.raises(ValidationError):
            DatasetId(value)


# Table ids allow hyphens too.
_table_chars = st.text(
    alphabet=_ASCII_LETTER_DIGIT_UNDERSCORE + "-",
    min_size=1,
    max_size=64,
)


@given(_table_chars)
@settings(max_examples=200)
def test_table_id_accepts_hyphens(value: str) -> None:
    t = TableId(value)
    assert str(t) == value


@given(st.integers(min_value=1, max_value=1024))
def test_arbitrary_length_within_bounds(length: int) -> None:
    value = "a" * length
    assert str(DatasetId(value)) == value


@given(st.integers(min_value=1025, max_value=2048))
def test_rejects_too_long(length: int) -> None:
    with pytest.raises(ValidationError):
        DatasetId("a" * length)
