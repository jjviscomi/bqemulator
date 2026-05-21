"""Unit tests for scripting control-flow signals."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.scripting.exceptions import (
    BreakSignal,
    ContinueSignal,
    ReturnSignal,
    ScriptRaise,
)

pytestmark = pytest.mark.unit


def test_break_signal_inherits_base() -> None:
    sig = BreakSignal()
    assert isinstance(sig, Exception)


def test_continue_signal_inherits_base() -> None:
    sig = ContinueSignal()
    assert isinstance(sig, Exception)


def test_return_signal_carries_value() -> None:
    sig = ReturnSignal(42)
    assert sig.value == 42


def test_return_signal_default_none() -> None:
    sig = ReturnSignal()
    assert sig.value is None


def test_script_raise_wraps_error() -> None:
    err = InvalidQueryError("boom")
    raised = ScriptRaise(err)
    assert raised.error is err
    assert raised.message == "boom"


def test_script_raise_message_override() -> None:
    err = InvalidQueryError("boom")
    raised = ScriptRaise(err, message_override="custom")
    assert raised.message == "custom"


def test_signals_can_be_raised_and_caught() -> None:
    with pytest.raises(BreakSignal):
        raise BreakSignal
    with pytest.raises(ContinueSignal):
        raise ContinueSignal
    with pytest.raises(ReturnSignal) as exc:
        raise ReturnSignal("v")
    assert exc.value.value == "v"
