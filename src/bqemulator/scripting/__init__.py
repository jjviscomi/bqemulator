"""BigQuery procedural scripting.

See [ADR 0011](../../docs/adr/0011-tree-walking-scripting-interpreter.md)
and [ADR 0015](../../docs/adr/0015-scripting-execution-model.md).
"""

from __future__ import annotations

from bqemulator.scripting.exceptions import (
    BreakSignal,
    ContinueSignal,
    ReturnSignal,
    ScriptRaise,
)
from bqemulator.scripting.frames import Frame, FrameStack, Variable
from bqemulator.scripting.interpreter import (
    ScriptInterpreter,
    ScriptResult,
    run_script,
)
from bqemulator.scripting.parser import parse_script

__all__ = [
    "BreakSignal",
    "ContinueSignal",
    "Frame",
    "FrameStack",
    "ReturnSignal",
    "ScriptInterpreter",
    "ScriptRaise",
    "ScriptResult",
    "Variable",
    "parse_script",
    "run_script",
]
