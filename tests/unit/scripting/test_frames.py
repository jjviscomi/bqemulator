"""Unit tests for the frame stack."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.scripting.frames import Frame, FrameStack, Variable

pytestmark = pytest.mark.unit


class TestFrameStack:
    def test_push_pop_tracks_depth(self) -> None:
        stack = FrameStack()
        assert stack.depth == 0
        stack.push("root")
        assert stack.depth == 1
        stack.push("block")
        assert stack.depth == 2
        stack.pop()
        assert stack.depth == 1

    def test_pop_underflow_raises(self) -> None:
        stack = FrameStack()
        with pytest.raises(InvalidQueryError, match="FrameStack underflow"):
            stack.pop()

    def test_declare_outside_frame_raises(self) -> None:
        stack = FrameStack()
        with pytest.raises(InvalidQueryError, match="declare without a frame"):
            stack.declare("x", "INT64")

    def test_declare_then_lookup(self) -> None:
        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 42)
        assert stack.lookup("x") == 42

    def test_declare_twice_raises(self) -> None:
        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 1)
        with pytest.raises(InvalidQueryError, match="already declared"):
            stack.declare("x", "INT64", 2)

    def test_set_existing_variable(self) -> None:
        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 1)
        stack.set("x", 99)
        assert stack.lookup("x") == 99

    def test_set_unknown_variable_raises(self) -> None:
        stack = FrameStack()
        stack.push("root")
        with pytest.raises(InvalidQueryError, match="Unknown variable"):
            stack.set("x", 1)

    def test_lookup_unknown_raises(self) -> None:
        stack = FrameStack()
        stack.push("root")
        with pytest.raises(InvalidQueryError, match="Unknown variable"):
            stack.lookup("x")

    def test_walk_outward_for_set(self) -> None:
        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 1)
        stack.push("block")
        stack.set("x", 99)  # walks outward to root frame
        stack.pop()
        assert stack.lookup("x") == 99

    def test_inner_frame_shadows_outer(self) -> None:
        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 1)
        stack.push("block")
        stack.declare("x", "INT64", 2)  # shadows outer
        assert stack.lookup("x") == 2
        stack.pop()
        assert stack.lookup("x") == 1

    def test_has_visible(self) -> None:
        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 1)
        assert stack.has("x")
        assert not stack.has("y")

    def test_all_visible_shadows(self) -> None:
        stack = FrameStack()
        stack.push("root")
        stack.declare("x", "INT64", 1)
        stack.declare("y", "INT64", 2)
        stack.push("block")
        stack.declare("x", "INT64", 99)
        visible = stack.all_visible()
        assert visible["x"].value == 99
        assert visible["y"].value == 2

    def test_frame_defaults(self) -> None:
        frame = Frame(kind="root")
        assert frame.variables == {}

    def test_variable_dataclass(self) -> None:
        v = Variable(name="x", type_name="INT64", value=42)
        assert v.name == "x"
        assert v.value == 42
