"""AST node definitions for the scripting interpreter.

Expressions inside scripting statements (in ``IF expr THEN``,
``SET name = expr``, etc.) are captured as raw BigQuery SQL text rather
than structured expression trees. The interpreter evaluates each
expression by wrapping it in ``SELECT <expr>`` and running it through
the existing SQL translation pipeline with script variables bound as
parameters. See [ADR 0015](../../docs/adr/0015-scripting-execution-model.md)
for the rationale.

All statements are frozen dataclasses to keep the AST immutable and
easily compared in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Statement:
    """Base class — frozen so instances are hashable for tests."""


@dataclass(frozen=True, slots=True)
class DeclareStmt(Statement):
    """``DECLARE name [, name]* TYPE [DEFAULT expr];``."""

    names: tuple[str, ...]
    type_name: str  # e.g. "INT64", "STRING", "ARRAY<INT64>"
    default_expr: str | None  # raw BQ expression or None


@dataclass(frozen=True, slots=True)
class SetStmt(Statement):
    """``SET name = expr;`` or ``SET (a, b) = (SELECT ...)``."""

    targets: tuple[str, ...]
    source_expr: str  # raw BQ expression or parenthesised SELECT


@dataclass(frozen=True, slots=True)
class IfStmt(Statement):
    """``IF expr THEN ... [ELSEIF expr THEN ...]* [ELSE ...] END IF;``."""

    branches: tuple[tuple[str, tuple[Statement, ...]], ...]
    else_body: tuple[Statement, ...] | None


@dataclass(frozen=True, slots=True)
class WhileStmt(Statement):
    """``WHILE expr DO ... END WHILE;``."""

    condition_expr: str
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class LoopStmt(Statement):
    """``LOOP ... END LOOP;``."""

    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class ForStmt(Statement):
    """``FOR name IN (SELECT ...) DO ... END FOR;``."""

    loop_var: str
    source_sql: str  # the (SELECT ...) subquery as raw BQ SQL
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class BreakStmt(Statement):
    """``BREAK;`` or ``LEAVE;``."""


@dataclass(frozen=True, slots=True)
class ContinueStmt(Statement):
    """``CONTINUE;`` or ``ITERATE;``."""


@dataclass(frozen=True, slots=True)
class ReturnStmt(Statement):
    """``RETURN [expr];``."""

    value_expr: str | None


@dataclass(frozen=True, slots=True)
class BeginStmt(Statement):
    """``BEGIN ... [EXCEPTION WHEN ERROR THEN ...] END;``."""

    body: tuple[Statement, ...]
    exception_handler: tuple[Statement, ...] | None


@dataclass(frozen=True, slots=True)
class CallStmt(Statement):
    """``CALL project.dataset.procedure(args);``."""

    routine_ref: str  # raw dotted name
    arg_exprs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecuteImmediateStmt(Statement):
    """``EXECUTE IMMEDIATE sql_expr [INTO names] [USING values];``."""

    sql_expr: str
    into_names: tuple[str, ...] = field(default_factory=tuple)
    using_exprs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RaiseStmt(Statement):
    """``RAISE [USING MESSAGE = 'msg'];``."""

    message_expr: str | None = None


@dataclass(frozen=True, slots=True)
class CreateFunctionStmt(Statement):
    """``CREATE [OR REPLACE] [TEMP] FUNCTION ... AS (body)``."""

    name: str  # dotted; may be 1-, 2-, or 3-part
    arguments: tuple[tuple[str, dict[str, Any] | None], ...]
    return_type: dict[str, Any] | None
    language: str  # "SQL" | "JAVASCRIPT"
    body: str
    or_replace: bool = False
    routine_type: str = "SCALAR_FUNCTION"  # "SCALAR_FUNCTION" | "TABLE_VALUED_FUNCTION"


@dataclass(frozen=True, slots=True)
class CreateProcedureStmt(Statement):
    """``CREATE [OR REPLACE] PROCEDURE ... BEGIN ... END;``."""

    name: str
    arguments: tuple[tuple[str, dict[str, Any] | None, str], ...]  # (name, type, mode)
    body: str  # raw body — parsed on first invocation
    or_replace: bool = False


@dataclass(frozen=True, slots=True)
class SqlStmt(Statement):
    """A pass-through SQL statement (SELECT, INSERT, etc.)."""

    sql: str


@dataclass(frozen=True, slots=True)
class Script:
    """Top-level parsed script."""

    statements: tuple[Statement, ...]


__all__ = [
    "BeginStmt",
    "BreakStmt",
    "CallStmt",
    "ContinueStmt",
    "CreateFunctionStmt",
    "CreateProcedureStmt",
    "DeclareStmt",
    "ExecuteImmediateStmt",
    "ForStmt",
    "IfStmt",
    "LoopStmt",
    "RaiseStmt",
    "ReturnStmt",
    "Script",
    "SetStmt",
    "SqlStmt",
    "Statement",
    "WhileStmt",
]
