"""Property tests for scripting frame scopes + exception propagation."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.scripting.frames import FrameStack

pytestmark = pytest.mark.property

_names = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_",
    min_size=1,
    max_size=8,
)
_values = st.integers()


@given(st.lists(_names, min_size=1, max_size=5, unique=True), _values)
def test_declare_values_roundtrip(names: list[str], value: int) -> None:
    stack = FrameStack()
    stack.push("root")
    for n in names:
        stack.declare(n, "INT64", value)
    for n in names:
        assert stack.lookup(n) == value


@given(st.lists(_values, min_size=1, max_size=10))
def test_set_overwrites(values: list[int]) -> None:
    stack = FrameStack()
    stack.push("root")
    stack.declare("x", "INT64", values[0])
    for v in values:
        stack.set("x", v)
    assert stack.lookup("x") == values[-1]


@given(_values, _values)
def test_shadowing_restores_outer(outer: int, inner: int) -> None:
    stack = FrameStack()
    stack.push("root")
    stack.declare("x", "INT64", outer)
    stack.push("block")
    stack.declare("x", "INT64", inner)
    assert stack.lookup("x") == inner
    stack.pop()
    assert stack.lookup("x") == outer


@given(st.lists(_names, min_size=1, max_size=3, unique=True))
def test_duplicate_declare_raises(names: list[str]) -> None:
    stack = FrameStack()
    stack.push("root")
    stack.declare(names[0], "INT64", 0)
    with pytest.raises(InvalidQueryError):
        stack.declare(names[0], "INT64", 1)


@given(_names)
def test_lookup_unknown_raises(name: str) -> None:
    stack = FrameStack()
    stack.push("root")
    with pytest.raises(InvalidQueryError):
        stack.lookup(name)


@given(st.integers(min_value=1, max_value=30))
def test_depth_tracks_push_pop(n: int) -> None:
    stack = FrameStack()
    for _ in range(n):
        stack.push("block")
    assert stack.depth == n
    for _ in range(n):
        stack.pop()
    assert stack.depth == 0
