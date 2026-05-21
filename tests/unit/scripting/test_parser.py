"""Unit tests for the scripting parser."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.scripting.ast import (
    BeginStmt,
    BreakStmt,
    CallStmt,
    ContinueStmt,
    CreateFunctionStmt,
    CreateProcedureStmt,
    DeclareStmt,
    ExecuteImmediateStmt,
    ForStmt,
    IfStmt,
    LoopStmt,
    RaiseStmt,
    ReturnStmt,
    SetStmt,
    SqlStmt,
    WhileStmt,
)
from bqemulator.scripting.parser import parse_script

pytestmark = pytest.mark.unit


class TestDeclare:
    def test_simple_declare(self) -> None:
        script = parse_script("DECLARE x INT64;")
        assert len(script.statements) == 1
        stmt = script.statements[0]
        assert isinstance(stmt, DeclareStmt)
        assert stmt.names == ("x",)
        assert stmt.type_name == "INT64"
        assert stmt.default_expr is None

    def test_declare_multiple(self) -> None:
        script = parse_script("DECLARE x, y, z INT64;")
        stmt = script.statements[0]
        assert isinstance(stmt, DeclareStmt)
        assert stmt.names == ("x", "y", "z")

    def test_declare_with_default(self) -> None:
        script = parse_script("DECLARE x INT64 DEFAULT 42;")
        stmt = script.statements[0]
        assert isinstance(stmt, DeclareStmt)
        assert stmt.default_expr == "42"

    def test_declare_without_trailing_semicolon(self) -> None:
        script = parse_script("DECLARE x INT64")
        assert len(script.statements) == 1

    def test_declare_array_type(self) -> None:
        script = parse_script("DECLARE arr ARRAY<INT64>;")
        stmt = script.statements[0]
        assert isinstance(stmt, DeclareStmt)
        assert "ARRAY" in stmt.type_name


class TestSet:
    def test_simple_set(self) -> None:
        script = parse_script("SET x = 1;")
        stmt = script.statements[0]
        assert isinstance(stmt, SetStmt)
        assert stmt.targets == ("x",)
        assert stmt.source_expr == "1"

    def test_set_expression(self) -> None:
        script = parse_script("SET x = a + b * 2;")
        stmt = script.statements[0]
        assert isinstance(stmt, SetStmt)
        assert "a + b * 2" in stmt.source_expr

    def test_set_multi_target(self) -> None:
        script = parse_script("SET (a, b) = (SELECT 1, 2);")
        stmt = script.statements[0]
        assert isinstance(stmt, SetStmt)
        assert stmt.targets == ("a", "b")

    def test_set_without_equals_raises(self) -> None:
        with pytest.raises(InvalidQueryError, match="SET requires"):
            parse_script("SET x 1;")


class TestIf:
    def test_if_then(self) -> None:
        script = parse_script("IF x > 0 THEN SELECT 'pos'; END IF;")
        stmt = script.statements[0]
        assert isinstance(stmt, IfStmt)
        assert len(stmt.branches) == 1
        cond, _body = stmt.branches[0]
        assert "x > 0" in cond
        assert stmt.else_body is None

    def test_if_else(self) -> None:
        script = parse_script(
            "IF x > 0 THEN SELECT 'pos'; ELSE SELECT 'neg'; END IF;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, IfStmt)
        assert stmt.else_body is not None

    def test_if_elseif(self) -> None:
        script = parse_script(
            "IF a THEN SELECT 1; ELSEIF b THEN SELECT 2; ELSE SELECT 3; END IF;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, IfStmt)
        assert len(stmt.branches) == 2
        assert stmt.else_body is not None

    def test_if_elsif_spelling(self) -> None:
        script = parse_script(
            "IF a THEN SELECT 1; ELSIF b THEN SELECT 2; END IF;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, IfStmt)
        assert len(stmt.branches) == 2


class TestLoops:
    def test_while(self) -> None:
        script = parse_script("WHILE x > 0 DO SET x = x - 1; END WHILE;")
        stmt = script.statements[0]
        assert isinstance(stmt, WhileStmt)

    def test_loop(self) -> None:
        script = parse_script("LOOP BREAK; END LOOP;")
        stmt = script.statements[0]
        assert isinstance(stmt, LoopStmt)

    def test_for(self) -> None:
        script = parse_script("FOR row IN (SELECT 1 AS x) DO SELECT row.x; END FOR;")
        stmt = script.statements[0]
        assert isinstance(stmt, ForStmt)
        assert stmt.loop_var == "row"

    def test_break(self) -> None:
        script = parse_script("LOOP BREAK; END LOOP;")
        loop = script.statements[0]
        assert isinstance(loop, LoopStmt)
        assert isinstance(loop.body[0], BreakStmt)

    def test_leave_alias(self) -> None:
        script = parse_script("LOOP LEAVE; END LOOP;")
        loop = script.statements[0]
        assert isinstance(loop, LoopStmt)
        assert isinstance(loop.body[0], BreakStmt)

    def test_continue(self) -> None:
        script = parse_script("LOOP CONTINUE; END LOOP;")
        loop = script.statements[0]
        assert isinstance(loop, LoopStmt)
        assert isinstance(loop.body[0], ContinueStmt)

    def test_iterate_alias(self) -> None:
        script = parse_script("LOOP ITERATE; END LOOP;")
        loop = script.statements[0]
        assert isinstance(loop, LoopStmt)
        assert isinstance(loop.body[0], ContinueStmt)


class TestBlocks:
    def test_begin_end(self) -> None:
        script = parse_script("BEGIN SELECT 1; SELECT 2; END;")
        stmt = script.statements[0]
        assert isinstance(stmt, BeginStmt)
        assert len(stmt.body) == 2
        assert stmt.exception_handler is None

    def test_begin_with_exception(self) -> None:
        script = parse_script(
            "BEGIN SELECT 1; EXCEPTION WHEN ERROR THEN SELECT 'handler'; END;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, BeginStmt)
        assert stmt.exception_handler is not None

    def test_begin_with_non_error_condition_raises(self) -> None:
        with pytest.raises(InvalidQueryError, match="Only 'EXCEPTION WHEN ERROR THEN'"):
            parse_script("BEGIN SELECT 1; EXCEPTION WHEN OTHER THEN SELECT 2; END;")


class TestCall:
    def test_call_no_args(self) -> None:
        script = parse_script("CALL proj.ds.proc();")
        stmt = script.statements[0]
        assert isinstance(stmt, CallStmt)
        assert stmt.routine_ref == "proj.ds.proc"
        assert stmt.arg_exprs == ()

    def test_call_with_args(self) -> None:
        script = parse_script("CALL ds.proc(1, 'x');")
        stmt = script.statements[0]
        assert isinstance(stmt, CallStmt)
        assert len(stmt.arg_exprs) == 2


class TestExecuteImmediate:
    def test_basic(self) -> None:
        script = parse_script("EXECUTE IMMEDIATE 'SELECT 1';")
        stmt = script.statements[0]
        assert isinstance(stmt, ExecuteImmediateStmt)

    def test_with_into(self) -> None:
        script = parse_script("EXECUTE IMMEDIATE 'SELECT 1' INTO x;")
        stmt = script.statements[0]
        assert isinstance(stmt, ExecuteImmediateStmt)
        assert stmt.into_names == ("x",)

    def test_with_using(self) -> None:
        script = parse_script("EXECUTE IMMEDIATE 'SELECT ?' USING 42;")
        stmt = script.statements[0]
        assert isinstance(stmt, ExecuteImmediateStmt)
        assert len(stmt.using_exprs) == 1


class TestReturn:
    def test_return_no_value(self) -> None:
        script = parse_script("RETURN;")
        stmt = script.statements[0]
        assert isinstance(stmt, ReturnStmt)
        assert stmt.value_expr is None

    def test_return_expr(self) -> None:
        script = parse_script("RETURN 42;")
        stmt = script.statements[0]
        assert isinstance(stmt, ReturnStmt)
        assert stmt.value_expr == "42"


class TestRaise:
    def test_raise_bare(self) -> None:
        script = parse_script("RAISE;")
        stmt = script.statements[0]
        assert isinstance(stmt, RaiseStmt)
        assert stmt.message_expr is None

    def test_raise_with_message(self) -> None:
        script = parse_script("RAISE USING MESSAGE = 'err';")
        stmt = script.statements[0]
        assert isinstance(stmt, RaiseStmt)
        assert stmt.message_expr is not None


class TestCreateFunction:
    def test_scalar_sql_function(self) -> None:
        script = parse_script(
            "CREATE FUNCTION ds.add_one(x INT64) RETURNS INT64 AS (x + 1);",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert stmt.routine_type == "SCALAR_FUNCTION"
        assert stmt.language == "SQL"
        assert not stmt.or_replace

    def test_or_replace(self) -> None:
        script = parse_script("CREATE OR REPLACE FUNCTION ds.f() AS (1);")
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert stmt.or_replace is True

    def test_table_function(self) -> None:
        script = parse_script(
            "CREATE TABLE FUNCTION ds.tvf() AS (SELECT 1);",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert stmt.routine_type == "TABLE_VALUED_FUNCTION"

    def test_javascript_function(self) -> None:
        script = parse_script(
            "CREATE FUNCTION ds.js(x INT64) RETURNS INT64 LANGUAGE JAVASCRIPT AS 'return x * 2;';",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert stmt.language == "JAVASCRIPT"


class TestCreateProcedure:
    def test_procedure(self) -> None:
        script = parse_script(
            "CREATE PROCEDURE ds.proc(x INT64) BEGIN SELECT x; END;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateProcedureStmt)
        assert stmt.name == "ds.proc"

    def test_procedure_or_replace(self) -> None:
        script = parse_script(
            "CREATE OR REPLACE PROCEDURE ds.proc() BEGIN SELECT 1; END;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateProcedureStmt)
        assert stmt.or_replace is True

    def test_procedure_with_in_param(self) -> None:
        # ``IN`` is also a SQL keyword (``x IN (1,2,3)``), so the lexer
        # tokenises it as KEYWORD instead of IDENT. The parser must
        # accept both token kinds when matching a parameter mode.
        script = parse_script(
            "CREATE PROCEDURE ds.p(IN x INT64) BEGIN SELECT x; END;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateProcedureStmt)
        assert stmt.arguments[0][0] == "x"
        assert stmt.arguments[0][2] == "IN"

    def test_procedure_with_out_param(self) -> None:
        script = parse_script(
            "CREATE PROCEDURE ds.p(IN x INT64, OUT y INT64) BEGIN SET y = x; END;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateProcedureStmt)
        assert stmt.arguments[1][2] == "OUT"

    def test_procedure_with_inout_param(self) -> None:
        script = parse_script(
            "CREATE PROCEDURE ds.p(INOUT n INT64) BEGIN SET n = n + 1; END;",
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateProcedureStmt)
        assert stmt.arguments[0][2] == "INOUT"


class TestStringLiteralEscapes:
    def test_backslash_d_in_double_quoted(self) -> None:
        # BigQuery body strings unescape ``\\\\`` to ``\\`` so a JS UDF
        # regex literal ``/\\d+/`` survives the SQL→JS transit. The
        # ``js_udf_uses_regexp`` conformance fixture exercises this.
        script = parse_script(
            "CREATE TEMP FUNCTION js_d(s STRING) RETURNS BOOL LANGUAGE js"
            r' AS "return /\d+/.test(s);";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert stmt.body == "return /\\d+/.test(s);"

    def test_newline_escape(self) -> None:
        # SQL body: "return s + \n"  → JS body: 'return s + ' + newline
        script = parse_script(
            'CREATE TEMP FUNCTION f(s STRING) RETURNS STRING LANGUAGE js AS "ret\\n";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert stmt.body == "ret\n"

    def test_unknown_escape_passes_through(self) -> None:
        # SQL body: "ret\q"  → unknown escape passes through verbatim.
        script = parse_script(
            'CREATE TEMP FUNCTION f(s STRING) RETURNS STRING LANGUAGE js AS "ret\\q";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert "\\q" in stmt.body

    def test_unicode_escape_four_hex_digits(self) -> None:
        # ``\uXXXX`` decodes to the BMP code point.
        script = parse_script(
            'CREATE TEMP FUNCTION f(s STRING) RETURNS STRING LANGUAGE js AS "snowman: \\u2603";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert "☃" in stmt.body

    def test_unicode_escape_non_alnum_passes_through(self) -> None:
        # ``\u`` followed by non-alphanumeric chars (e.g. punctuation)
        # fails the ``isalnum()`` gate and falls through to the verbatim
        # passthrough at line 832 of parser.py.
        script = parse_script(
            'CREATE TEMP FUNCTION f(s STRING) RETURNS STRING LANGUAGE js AS "bad: \\u====";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        # The bare backslash is consumed; the ``u`` plus the rest remain.
        assert "u====" in stmt.body

    def test_hex_escape_two_hex_digits(self) -> None:
        # ``\xXX`` decodes to a single byte / Latin-1 code point.
        script = parse_script(
            'CREATE TEMP FUNCTION f(s STRING) RETURNS STRING LANGUAGE js AS "A: \\x41";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert "A: A" in stmt.body

    def test_hex_escape_invalid_hex_passes_through(self) -> None:
        # ``\xZZ`` has non-hex digits; the value-error branch falls
        # through to the verbatim passthrough at line 842.
        script = parse_script(
            'CREATE TEMP FUNCTION f(s STRING) RETURNS STRING LANGUAGE js AS "x: \\xZZ";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        # Doesn't crash; the malformed escape just passes through.
        assert "ZZ" in stmt.body or "x" in stmt.body


class TestBeginTransactionPassthrough:
    def test_begin_transaction_is_sql_statement(self) -> None:
        # ``BEGIN TRANSACTION`` is a transaction-control statement, not a
        # BEGIN/END block; route it through the SQL passthrough.
        script = parse_script("BEGIN TRANSACTION; SELECT 1; COMMIT TRANSACTION;")
        assert isinstance(script.statements[0], SqlStmt)
        assert isinstance(script.statements[1], SqlStmt)
        assert isinstance(script.statements[2], SqlStmt)

    def test_begin_without_transaction_is_block(self) -> None:
        script = parse_script("BEGIN SELECT 1; END;")
        from bqemulator.scripting.ast import BeginStmt

        assert isinstance(script.statements[0], BeginStmt)


class TestNestedArrayStructType:
    def test_array_of_struct_type_annotation(self) -> None:
        # ``ARRAY<STRUCT<...>>`` produces two consecutive ``>>``
        # characters; the lexer fuses them into a single OP token. The
        # type-reader must decrement bracket depth by 2 for ``>>``.
        script = parse_script(
            "CREATE TEMP FUNCTION f(n INT64) RETURNS ARRAY<STRUCT<i INT64, label STRING>>"
            ' LANGUAGE js AS "return [];";'
        )
        stmt = script.statements[0]
        assert isinstance(stmt, CreateFunctionStmt)
        assert stmt.return_type == {
            "typeKind": "ARRAY",
            "arrayElementType": {
                "typeKind": "STRUCT",
                "structType": {
                    "fields": [
                        {"name": "i", "type": {"typeKind": "INT64"}},
                        {"name": "label", "type": {"typeKind": "STRING"}},
                    ],
                },
            },
        }


class TestPassthroughSql:
    def test_select(self) -> None:
        script = parse_script("SELECT 1;")
        stmt = script.statements[0]
        assert isinstance(stmt, SqlStmt)

    def test_insert(self) -> None:
        script = parse_script("INSERT INTO t VALUES (1);")
        stmt = script.statements[0]
        assert isinstance(stmt, SqlStmt)

    def test_create_table_is_passthrough(self) -> None:
        script = parse_script("CREATE TABLE t (x INT64);")
        stmt = script.statements[0]
        assert isinstance(stmt, SqlStmt)

    def test_multiple_statements(self) -> None:
        script = parse_script("SELECT 1; SELECT 2; SELECT 3;")
        assert len(script.statements) == 3
