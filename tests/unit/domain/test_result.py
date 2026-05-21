"""Tests for the Result type."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.domain.result import Err, Ok

pytestmark = pytest.mark.unit


class TestOk:
    def test_is_ok_true_is_err_false(self) -> None:
        ok = Ok(42)
        assert ok.is_ok()
        assert not ok.is_err()

    def test_unwrap_returns_value(self) -> None:
        assert Ok("hi").unwrap() == "hi"

    def test_map_applies_function(self) -> None:
        result = Ok(2).map(lambda x: x + 1)
        assert result.unwrap() == 3


class TestErr:
    def test_is_ok_false_is_err_true(self) -> None:
        err = Err(InvalidQueryError("bad"))
        assert not err.is_ok()
        assert err.is_err()

    def test_unwrap_raises_contained_error(self) -> None:
        err = Err(InvalidQueryError("bad sql"))
        with pytest.raises(InvalidQueryError, match="bad sql"):
            err.unwrap()

    def test_map_is_noop(self) -> None:
        original = Err(InvalidQueryError("bad"))
        assert original.map(lambda x: x).error is original.error


class TestPatternMatching:
    def test_ok_branch(self) -> None:
        result: Ok[int] | Err[InvalidQueryError] = Ok(7)
        match result:
            case Ok(value):
                assert value == 7
            case Err(_):
                pytest.fail("took wrong branch")

    def test_err_branch(self) -> None:
        err_val = InvalidQueryError("bad")
        result: Ok[int] | Err[InvalidQueryError] = Err(err_val)
        match result:
            case Ok(_):
                pytest.fail("took wrong branch")
            case Err(error):
                assert error is err_val
