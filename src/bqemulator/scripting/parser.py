"""Recursive-descent parser for BigQuery scripts.

Produces a :class:`Script` made of typed :class:`Statement` nodes.
Expressions inside scripting statements are captured as raw source
text and evaluated by the interpreter through the SQL translator with
script variables bound as parameters — see
[ADR 0015](../../docs/adr/0015-scripting-execution-model.md).
"""

from __future__ import annotations

from typing import Any

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
    Script,
    SetStmt,
    SqlStmt,
    Statement,
    WhileStmt,
)
from bqemulator.scripting.lexer import Token, tokenize
from bqemulator.udf.types import parse_bq_type_string

# Tuple length for the (name, type, mode) form used by procedure args.
_PROCEDURE_ARG_TUPLE_LEN = 3

# Keywords that start a known scripting construct.
_SCRIPTING_STARTERS: frozenset[str] = frozenset(
    {
        "DECLARE",
        "SET",
        "IF",
        "WHILE",
        "LOOP",
        "FOR",
        "BREAK",
        "LEAVE",
        "CONTINUE",
        "ITERATE",
        "BEGIN",
        "CALL",
        "EXECUTE",
        "RETURN",
        "RAISE",
    },
)


class _Cursor:
    """Token-stream cursor with raw-source recall."""

    def __init__(self, source: str, tokens: list[Token]) -> None:
        self._source = source
        self._tokens = tokens
        self._pos = 0

    @property
    def pos(self) -> int:
        """Return the current token index."""
        return self._pos

    @property
    def current(self) -> Token:
        """Return the token at the cursor."""
        return self._tokens[self._pos]

    def peek(self, offset: int = 1) -> Token:
        """Return a future token without advancing."""
        idx = min(self._pos + offset, len(self._tokens) - 1)
        return self._tokens[idx]

    def advance(self) -> Token:
        """Consume and return the current token."""
        tok = self._tokens[self._pos]
        if tok.kind != "EOF":
            self._pos += 1
        return tok

    def at_eof(self) -> bool:
        """Return whether the cursor has reached EOF."""
        return self._tokens[self._pos].kind == "EOF"

    def match_keyword(self, *kws: str) -> bool:
        """Return whether the current token matches any of the given keywords."""
        return self.current.kind == "KEYWORD" and self.current.value in kws

    def expect_keyword(self, kw: str) -> None:
        """Consume ``kw`` or raise."""
        if not self.match_keyword(kw):
            raise InvalidQueryError(
                f"Expected keyword {kw!r}, got {self.current.kind}:{self.current.value!r}",
            )
        self.advance()

    def expect_punct(self, ch: str) -> None:
        """Consume a specific punctuation or raise."""
        if self.current.kind != "PUNCT" or self.current.value != ch:
            raise InvalidQueryError(
                f"Expected {ch!r}, got {self.current.kind}:{self.current.value!r}",
            )
        self.advance()

    def slice(self, start: int, end: int) -> str:
        """Return the raw source between two token offsets."""
        if start >= len(self._tokens) or end <= start:
            return ""
        # Source bytes from the first token's start to the last consumed token's end.
        begin_byte = self._tokens[start].start
        end_byte = self._tokens[end - 1].end
        return self._source[begin_byte:end_byte].strip()


class Parser:
    """BigQuery scripting parser."""

    def __init__(self, source: str) -> None:
        self._source = source
        tokens = tokenize(source)
        self._cursor = _Cursor(source, tokens)

    def parse_script(self) -> Script:
        """Parse a top-level script body."""
        stmts = self._parse_statements(stop_keywords=())
        return Script(statements=tuple(stmts))

    # -- Top-level dispatch ----------------------------------------------

    def _parse_statements(self, stop_keywords: tuple[str, ...]) -> list[Statement]:
        """Parse a sequence of statements until EOF or any stop keyword."""
        stmts: list[Statement] = []
        while not self._cursor.at_eof():
            if self._cursor.match_keyword(*stop_keywords):
                break
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
        return stmts

    def _parse_statement(self) -> Statement | None:
        tok = self._cursor.current
        if tok.kind == "PUNCT" and tok.value == ";":
            self._cursor.advance()
            return None

        if tok.kind == "KEYWORD":
            kw = tok.value
            if kw == "DECLARE":
                return self._parse_declare()
            if kw == "SET":
                return self._parse_set()
            if kw == "IF":
                return self._parse_if()
            if kw == "WHILE":
                return self._parse_while()
            if kw == "LOOP":
                return self._parse_loop()
            if kw == "FOR":
                return self._parse_for()
            if kw in ("BREAK", "LEAVE"):
                self._cursor.advance()
                self._consume_optional_semicolon()
                return BreakStmt()
            if kw in ("CONTINUE", "ITERATE"):
                self._cursor.advance()
                self._consume_optional_semicolon()
                return ContinueStmt()
            if kw == "BEGIN":
                # ``BEGIN TRANSACTION`` / ``BEGIN TRANSACTION;`` is a
                # transaction-control statement, NOT a BEGIN/END block.
                # Route it through the generic SQL statement parser
                # (which the engine handles via DuckDB's own
                # transaction model). The peek-ahead is cheap and safer
                # than asking ``_parse_begin`` to lookahead.
                next_tok = self._cursor.peek(1)
                if next_tok.kind == "KEYWORD" and next_tok.value.upper() == "TRANSACTION":
                    return self._parse_sql_statement()
                if next_tok.kind == "IDENT" and next_tok.value.upper() == "TRANSACTION":
                    return self._parse_sql_statement()
                return self._parse_begin()
            if kw == "CALL":
                return self._parse_call()
            if kw == "EXECUTE":
                return self._parse_execute_immediate()
            if kw == "RETURN":
                return self._parse_return()
            if kw == "RAISE":
                return self._parse_raise()
            if kw == "CREATE":
                if self._is_create_routine():
                    return self._parse_create_routine()
                return self._parse_sql_statement()

        return self._parse_sql_statement()

    # -- Individual constructs -------------------------------------------

    def _parse_declare(self) -> DeclareStmt:
        self._cursor.expect_keyword("DECLARE")
        names = [self._read_identifier_value()]
        while self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ",":
            self._cursor.advance()
            names.append(self._read_identifier_value())

        type_name = self._read_type_name()

        default_expr: str | None = None
        if self._cursor.match_keyword("DEFAULT"):
            self._cursor.advance()
            default_expr = self._capture_expr_until((";",), stop_keywords=())

        self._consume_optional_semicolon()
        return DeclareStmt(
            names=tuple(names),
            type_name=type_name,
            default_expr=default_expr,
        )

    def _parse_set(self) -> SetStmt:
        self._cursor.expect_keyword("SET")

        targets: list[str] = []
        if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == "(":
            self._cursor.advance()
            targets.append(self._read_identifier_value())
            while self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ",":
                self._cursor.advance()
                targets.append(self._read_identifier_value())
            self._cursor.expect_punct(")")
        else:
            targets.append(self._read_identifier_value())

        if self._cursor.current.kind != "OP" or self._cursor.current.value != "=":
            raise InvalidQueryError("SET requires '=' after target(s)")
        self._cursor.advance()
        source_expr = self._capture_expr_until((";",), stop_keywords=())
        self._consume_optional_semicolon()
        return SetStmt(targets=tuple(targets), source_expr=source_expr)

    def _parse_if(self) -> IfStmt:
        self._cursor.expect_keyword("IF")
        branches: list[tuple[str, tuple[Statement, ...]]] = []
        else_body: tuple[Statement, ...] | None = None

        cond = self._capture_expr_until((), stop_keywords=("THEN",))
        self._cursor.expect_keyword("THEN")
        body = self._parse_statements(("ELSEIF", "ELSIF", "ELSE", "END"))
        branches.append((cond, tuple(body)))

        while self._cursor.match_keyword("ELSEIF", "ELSIF"):
            self._cursor.advance()
            cond = self._capture_expr_until((), stop_keywords=("THEN",))
            self._cursor.expect_keyword("THEN")
            body = self._parse_statements(("ELSEIF", "ELSIF", "ELSE", "END"))
            branches.append((cond, tuple(body)))

        if self._cursor.match_keyword("ELSE"):
            self._cursor.advance()
            else_body = tuple(self._parse_statements(("END",)))

        self._cursor.expect_keyword("END")
        self._cursor.expect_keyword("IF")
        self._consume_optional_semicolon()
        return IfStmt(branches=tuple(branches), else_body=else_body)

    def _parse_while(self) -> WhileStmt:
        self._cursor.expect_keyword("WHILE")
        cond = self._capture_expr_until((), stop_keywords=("DO",))
        self._cursor.expect_keyword("DO")
        body = self._parse_statements(("END",))
        self._cursor.expect_keyword("END")
        self._cursor.expect_keyword("WHILE")
        self._consume_optional_semicolon()
        return WhileStmt(condition_expr=cond, body=tuple(body))

    def _parse_loop(self) -> LoopStmt:
        self._cursor.expect_keyword("LOOP")
        body = self._parse_statements(("END",))
        self._cursor.expect_keyword("END")
        self._cursor.expect_keyword("LOOP")
        self._consume_optional_semicolon()
        return LoopStmt(body=tuple(body))

    def _parse_for(self) -> ForStmt:
        self._cursor.expect_keyword("FOR")
        loop_var = self._read_identifier_value()
        self._cursor.expect_keyword("IN")
        source_sql = self._capture_expr_until((), stop_keywords=("DO",))
        self._cursor.expect_keyword("DO")
        body = self._parse_statements(("END",))
        self._cursor.expect_keyword("END")
        self._cursor.expect_keyword("FOR")
        self._consume_optional_semicolon()
        return ForStmt(loop_var=loop_var, source_sql=source_sql, body=tuple(body))

    def _parse_begin(self) -> BeginStmt:
        self._cursor.expect_keyword("BEGIN")
        body = self._parse_statements(("EXCEPTION", "END"))
        exception_handler: tuple[Statement, ...] | None = None
        if self._cursor.match_keyword("EXCEPTION"):
            self._cursor.advance()
            self._cursor.expect_keyword("WHEN")
            # For only `WHEN ERROR THEN` is supported — matches any
            # DomainError. Future phases can widen to specific conditions.
            if not (
                self._cursor.current.kind == "IDENT"
                and self._cursor.current.value.upper() == "ERROR"
            ):
                raise InvalidQueryError("Only 'EXCEPTION WHEN ERROR THEN' is supported")
            self._cursor.advance()
            self._cursor.expect_keyword("THEN")
            exception_handler = tuple(self._parse_statements(("END",)))
        self._cursor.expect_keyword("END")
        self._consume_optional_semicolon()
        return BeginStmt(body=tuple(body), exception_handler=exception_handler)

    def _parse_call(self) -> CallStmt:
        self._cursor.expect_keyword("CALL")
        ref = self._read_dotted_name()
        self._cursor.expect_punct("(")
        args = self._capture_arg_list()
        self._cursor.expect_punct(")")
        self._consume_optional_semicolon()
        return CallStmt(routine_ref=ref, arg_exprs=tuple(args))

    def _parse_execute_immediate(self) -> ExecuteImmediateStmt:
        self._cursor.expect_keyword("EXECUTE")
        self._cursor.expect_keyword("IMMEDIATE")
        sql_expr = self._capture_expr_until((";",), stop_keywords=("INTO", "USING"))
        into_names: list[str] = []
        using_exprs: list[str] = []
        if self._cursor.match_keyword("INTO"):
            self._cursor.advance()
            into_names.append(self._read_identifier_value())
            while self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ",":
                self._cursor.advance()
                into_names.append(self._read_identifier_value())
        if self._cursor.match_keyword("USING"):
            self._cursor.advance()
            using_exprs = self._capture_comma_list_until((";",), stop_keywords=())
        self._consume_optional_semicolon()
        return ExecuteImmediateStmt(
            sql_expr=sql_expr,
            into_names=tuple(into_names),
            using_exprs=tuple(using_exprs),
        )

    def _parse_return(self) -> ReturnStmt:
        self._cursor.expect_keyword("RETURN")
        if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ";":
            self._cursor.advance()
            return ReturnStmt(value_expr=None)
        value = self._capture_expr_until((";",), stop_keywords=())
        self._consume_optional_semicolon()
        return ReturnStmt(value_expr=value)

    def _parse_raise(self) -> RaiseStmt:
        self._cursor.expect_keyword("RAISE")
        msg_expr: str | None = None
        if self._cursor.match_keyword("USING"):
            self._cursor.advance()
            if (
                self._cursor.current.kind != "IDENT"
                or self._cursor.current.value.upper() != "MESSAGE"
            ):
                raise InvalidQueryError("RAISE USING requires MESSAGE = <expr>")
            self._cursor.advance()
            if self._cursor.current.kind != "OP" or self._cursor.current.value != "=":
                raise InvalidQueryError("RAISE USING MESSAGE requires '='")
            self._cursor.advance()
            msg_expr = self._capture_expr_until((";",), stop_keywords=())
        self._consume_optional_semicolon()
        return RaiseStmt(message_expr=msg_expr)

    # -- CREATE FUNCTION / PROCEDURE --------------------------------------

    def _is_create_routine(self) -> bool:
        """Look ahead to distinguish CREATE FUNCTION/PROCEDURE from other DDL."""
        i = 1
        tokens_left = 5
        while tokens_left > 0:
            tok = self._cursor.peek(i)
            if tok.kind == "EOF":
                return False
            if tok.kind == "KEYWORD":
                if tok.value in ("FUNCTION", "PROCEDURE"):
                    return True
                if tok.value in ("OR", "REPLACE", "TEMP", "TEMPORARY", "TABLE"):
                    i += 1
                    tokens_left -= 1
                    continue
                return False
            if tok.kind == "IDENT" and tok.value.upper() in ("TEMP", "TEMPORARY"):
                i += 1
                tokens_left -= 1
                continue
            return False
        return False

    def _parse_create_routine(self) -> Statement:
        self._cursor.expect_keyword("CREATE")
        or_replace = False
        if self._cursor.match_keyword("OR"):
            self._cursor.advance()
            self._cursor.expect_keyword("REPLACE")
            or_replace = True
        is_temp = False
        if self._cursor.match_keyword("TEMP", "TEMPORARY"):
            self._cursor.advance()
            is_temp = True

        is_table_fn = False
        if self._cursor.match_keyword("TABLE"):
            self._cursor.advance()
            is_table_fn = True

        if self._cursor.match_keyword("FUNCTION"):
            self._cursor.advance()
            return self._parse_function_rest(or_replace=or_replace, table_fn=is_table_fn)
        if self._cursor.match_keyword("PROCEDURE"):
            self._cursor.advance()
            return self._parse_procedure_rest(or_replace=or_replace)
        raise InvalidQueryError(
            f"CREATE {'TEMP ' if is_temp else ''}... expected FUNCTION or PROCEDURE",
        )

    def _parse_function_rest(self, *, or_replace: bool, table_fn: bool) -> CreateFunctionStmt:
        name = self._read_dotted_name()
        args = self._parse_routine_arg_list(for_procedure=False)
        return_type: dict[str, Any] | None = None
        if self._cursor.match_keyword("RETURNS"):
            self._cursor.advance()
            return_type = parse_bq_type_string(self._read_type_name())

        language = "SQL"
        body = ""
        if self._cursor.match_keyword("LANGUAGE"):
            self._cursor.advance()
            lang_tok = self._cursor.advance()
            raw_lang = lang_tok.value.upper()
            # BigQuery accepts ``LANGUAGE js`` as a synonym for
            # ``LANGUAGE JAVASCRIPT`` (the wire-format / REST API and
            # ``RoutineMeta.language`` only allow ``SQL`` / ``JAVASCRIPT``).
            language = "JAVASCRIPT" if raw_lang == "JS" else raw_lang
            self._cursor.expect_keyword("AS")
            body = self._read_string_literal()
        elif self._cursor.match_keyword("AS"):
            self._cursor.advance()
            # TVF bodies can be either ``AS (SELECT ...)`` (parenthesised)
            # or ``AS SELECT ...`` (bare). Scalar UDFs always use the
            # parenthesised form. For the bare form, capture the rest
            # of the statement (until ``;`` or EOF) as the body.
            if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == "(":
                body = self._read_parenthesised_body()
            elif table_fn:
                body = self._read_bare_statement_body()
            else:
                raise InvalidQueryError("Expected '(' after AS")
        else:
            raise InvalidQueryError("CREATE FUNCTION requires AS or LANGUAGE ... AS")

        self._consume_optional_semicolon()
        return CreateFunctionStmt(
            name=name,
            arguments=tuple(args),
            return_type=return_type,
            language=language,
            body=body,
            or_replace=or_replace,
            routine_type="TABLE_VALUED_FUNCTION" if table_fn else "SCALAR_FUNCTION",
        )

    def _parse_procedure_rest(self, *, or_replace: bool) -> CreateProcedureStmt:
        name = self._read_dotted_name()
        args = self._parse_routine_arg_list(for_procedure=True)
        self._cursor.expect_keyword("BEGIN")
        # Capture raw body until the matching END; BEGIN/END nest.
        body = self._capture_balanced_block()
        self._consume_optional_semicolon()
        return CreateProcedureStmt(
            name=name,
            arguments=tuple(a for a in args if len(a) == _PROCEDURE_ARG_TUPLE_LEN),
            body=body,
            or_replace=or_replace,
        )

    def _parse_routine_arg_list(
        self,
        *,
        for_procedure: bool,
    ) -> list[tuple[str, dict[str, Any] | None] | tuple[str, dict[str, Any] | None, str]]:
        self._cursor.expect_punct("(")
        out: list[tuple[str, dict[str, Any] | None] | tuple[str, dict[str, Any] | None, str]] = []
        if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ")":
            self._cursor.advance()
            return out
        while True:
            mode = "IN"
            # ``IN`` is also a SQL keyword (used in ``x IN (...)`` predicates) so
            # the lexer tokenises it as KEYWORD; ``OUT`` / ``INOUT`` come back
            # as IDENT. Accept both kinds when the value matches a recognised
            # parameter mode.
            current = self._cursor.current
            if current.kind in ("IDENT", "KEYWORD") and current.value.upper() in (
                "IN",
                "OUT",
                "INOUT",
            ):
                mode = current.value.upper()
                self._cursor.advance()
            name = self._read_identifier_value()
            type_name = self._read_type_name()
            bq_type = parse_bq_type_string(type_name)
            if for_procedure:
                out.append((name, bq_type, mode))
            else:
                out.append((name, bq_type))
            if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ",":
                self._cursor.advance()
                continue
            break
        self._cursor.expect_punct(")")
        return out

    def _read_bare_statement_body(self) -> str:
        """Capture the bare ``SELECT ...`` body of a TVF (no surrounding parens).

        BigQuery accepts ``CREATE TABLE FUNCTION f(...) AS SELECT ...``
        (no parens around the SELECT). Read until the trailing ``;`` or
        EOF, paren-balanced so subqueries inside the body are captured
        intact.
        """
        start_tok = self._cursor.pos
        depth = 0
        while not self._cursor.at_eof():
            tok = self._cursor.current
            if tok.kind == "PUNCT" and tok.value == "(":
                depth += 1
            elif tok.kind == "PUNCT" and tok.value == ")":
                depth -= 1
            elif tok.kind == "PUNCT" and tok.value == ";" and depth == 0:
                end_tok = self._cursor.pos
                return self._cursor.slice(start_tok, end_tok).strip()
            self._cursor.advance()
        return self._cursor.slice(start_tok, self._cursor.pos).strip()

    def _read_parenthesised_body(self) -> str:
        """Capture the text inside the outer ``(...)`` after ``AS``."""
        if self._cursor.current.kind != "PUNCT" or self._cursor.current.value != "(":
            raise InvalidQueryError("Expected '(' after AS")
        start_tok = self._cursor.pos
        self._cursor.advance()
        depth = 1
        while not self._cursor.at_eof():
            tok = self._cursor.current
            if tok.kind == "PUNCT" and tok.value == "(":
                depth += 1
            elif tok.kind == "PUNCT" and tok.value == ")":
                depth -= 1
                if depth == 0:
                    end_tok = self._cursor.pos + 1
                    self._cursor.advance()
                    # Strip the outer parens.
                    inner = self._cursor.slice(start_tok + 1, end_tok - 1)
                    return inner.strip()
            self._cursor.advance()
        raise InvalidQueryError("Unterminated AS (...) body")

    def _capture_balanced_block(self) -> str:
        """Capture raw source between BEGIN and matching END."""
        start = self._cursor.pos
        depth = 1
        while not self._cursor.at_eof():
            tok = self._cursor.current
            if tok.kind == "KEYWORD":
                if tok.value == "BEGIN":
                    depth += 1
                elif tok.value == "END":
                    depth -= 1
                    if depth == 0:
                        end = self._cursor.pos
                        body = self._cursor.slice(start, end)
                        self._cursor.advance()  # consume END
                        return body
            self._cursor.advance()
        raise InvalidQueryError("Unterminated BEGIN ... END block")

    # -- Pass-through SQL ------------------------------------------------

    def _parse_sql_statement(self) -> SqlStmt:
        start = self._cursor.pos
        depth = 0
        while not self._cursor.at_eof():
            tok = self._cursor.current
            if tok.kind == "PUNCT" and tok.value == "(":
                depth += 1
            elif tok.kind == "PUNCT" and tok.value == ")":
                depth -= 1
            elif tok.kind == "PUNCT" and tok.value == ";" and depth == 0:
                end = self._cursor.pos
                sql = self._cursor.slice(start, end)
                self._cursor.advance()
                return SqlStmt(sql=sql)
            self._cursor.advance()
        end = self._cursor.pos
        sql = self._cursor.slice(start, end)
        return SqlStmt(sql=sql)

    # -- Identifier + expression helpers ---------------------------------

    def _read_identifier_value(self) -> str:
        tok = self._cursor.current
        if tok.kind != "IDENT":
            raise InvalidQueryError(
                f"Expected identifier, got {tok.kind}:{tok.value!r}",
            )
        self._cursor.advance()
        return tok.value

    def _read_dotted_name(self) -> str:
        parts = [self._read_identifier_value()]
        while self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ".":
            self._cursor.advance()
            parts.append(self._read_identifier_value())
        return ".".join(parts)

    def _read_string_literal(self) -> str:
        tok = self._cursor.current
        if tok.kind != "STRING":
            raise InvalidQueryError(f"Expected string literal, got {tok.kind}:{tok.value!r}")
        self._cursor.advance()
        return _unquote_string(tok.value)

    def _read_type_name(self) -> str:
        """Read a BigQuery type annotation — e.g. ``INT64``, ``ARRAY<INT64>``.

        The lexer fuses two consecutive ``>`` characters into a single
        ``>>`` OP token (it is also the right-shift operator), so this
        method must dispatch on the literal value to recover the closing
        bracket depth: ``>>`` decrements depth by 2 and ``>>>`` by 3.
        """
        start = self._cursor.pos
        depth = 0
        consumed_any = False
        while not self._cursor.at_eof():
            tok = self._cursor.current
            if tok.kind == "OP" and tok.value == "<":
                depth += 1
                consumed_any = True
                self._cursor.advance()
                continue
            if tok.kind == "OP" and set(tok.value) == {">"} and tok.value:
                close_count = len(tok.value)
                if depth == 0:
                    break
                if close_count > depth:
                    # We can only "consume" min(depth, close_count)
                    # closing brackets, but since the lexer gives us a
                    # single token we have to consume the whole thing —
                    # mismatched bracket depth in user input.
                    raise InvalidQueryError(
                        f"Type annotation has more closing brackets than openings: {tok.value!r}"
                    )
                depth -= close_count
                consumed_any = True
                self._cursor.advance()
                continue
            if depth > 0:
                consumed_any = True
                self._cursor.advance()
                continue
            if tok.kind in ("IDENT", "KEYWORD") and not consumed_any:
                consumed_any = True
                self._cursor.advance()
                # DECIMAL(38,9)-style precision
                if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == "(":
                    self._cursor.advance()
                    inner_depth = 1
                    while not self._cursor.at_eof() and inner_depth > 0:
                        inner = self._cursor.current
                        if inner.kind == "PUNCT" and inner.value == "(":
                            inner_depth += 1
                        elif inner.kind == "PUNCT" and inner.value == ")":
                            inner_depth -= 1
                        self._cursor.advance()
                continue
            break
        if not consumed_any:
            raise InvalidQueryError(f"Expected type name, got {self._cursor.current.kind}")
        return self._cursor.slice(start, self._cursor.pos).strip()

    def _capture_expr_until(
        self,
        stop_puncts: tuple[str, ...],
        *,
        stop_keywords: tuple[str, ...],
    ) -> str:
        """Capture raw source until a top-level stop punct / keyword.

        Does not consume the terminator.
        """
        start = self._cursor.pos
        depth = 0
        while not self._cursor.at_eof():
            tok = self._cursor.current
            if tok.kind == "PUNCT" and tok.value == "(":
                depth += 1
            elif tok.kind == "PUNCT" and tok.value == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and (
                (tok.kind == "PUNCT" and tok.value in stop_puncts)
                or (tok.kind == "KEYWORD" and tok.value in stop_keywords)
            ):
                break
            self._cursor.advance()
        end = self._cursor.pos
        return self._cursor.slice(start, end)

    def _capture_comma_list_until(
        self,
        stop_puncts: tuple[str, ...],
        *,
        stop_keywords: tuple[str, ...],
    ) -> list[str]:
        """Capture a comma-separated expression list."""
        items: list[str] = []
        while not self._cursor.at_eof():
            expr = self._capture_expr_until(
                (*stop_puncts, ","),
                stop_keywords=stop_keywords,
            )
            if expr:
                items.append(expr)
            if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ",":
                self._cursor.advance()
                continue
            break
        return items

    def _capture_arg_list(self) -> list[str]:
        """Capture a comma-separated argument list inside ``( ... )``."""
        items: list[str] = []
        depth = 1  # assume caller already consumed the outer "("
        start = self._cursor.pos
        while not self._cursor.at_eof():
            tok = self._cursor.current
            if tok.kind == "PUNCT" and tok.value == "(":
                depth += 1
            elif tok.kind == "PUNCT" and tok.value == ")":
                depth -= 1
                if depth == 0:
                    slice_text = self._cursor.slice(start, self._cursor.pos).strip()
                    if slice_text:
                        items.append(slice_text)
                    return items
            elif tok.kind == "PUNCT" and tok.value == "," and depth == 1:
                slice_text = self._cursor.slice(start, self._cursor.pos).strip()
                items.append(slice_text)
                self._cursor.advance()
                start = self._cursor.pos
                continue
            self._cursor.advance()
        raise InvalidQueryError("Unterminated argument list")

    def _consume_optional_semicolon(self) -> None:
        if self._cursor.current.kind == "PUNCT" and self._cursor.current.value == ";":
            self._cursor.advance()


def parse_script(source: str) -> Script:
    """Parse ``source`` into a :class:`Script`."""
    return Parser(source).parse_script()


def _unquote_string(raw: str) -> str:
    """Strip the surrounding quotes + decode BigQuery string-literal escapes.

    BigQuery quoted-string literals (single, double, or triple-quoted)
    interpret backslash escape sequences exactly like Python's standard
    string syntax. The lexer captures the source bytes verbatim — this
    helper strips the quotes and decodes the escapes so downstream
    consumers (JS UDF bodies, SQL UDF bodies, scripting RAISE messages)
    see the same string BigQuery sees.
    """
    inner: str
    if (raw.startswith("'''") and raw.endswith("'''")) or (
        raw.startswith('"""') and raw.endswith('"""')
    ):
        inner = raw[3:-3]
    elif (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        inner = raw[1:-1]
    else:
        return raw
    if "\\" not in inner:
        return inner
    return _decode_bq_string_escapes(inner)


def _decode_bq_string_escapes(s: str) -> str:
    r"""Decode BigQuery string-literal escape sequences in ``s``.

    Recognised sequences (per BigQuery's quoted-literal grammar):
    ``\\``, ``\'``, ``\"``, ``\n``, ``\t``, ``\r``, ``\b``,
    ``\f``, ``\v``, ``\0``, ``\a``, plus ``\u{XXXX}`` and
    ``\uXXXX``. Unknown escapes fall through as the literal
    two-character sequence, matching BigQuery's permissive behaviour.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = s[i + 1]
        mapping = {
            "\\": "\\",
            "'": "'",
            '"': '"',
            "n": "\n",
            "t": "\t",
            "r": "\r",
            "b": "\b",
            "f": "\f",
            "v": "\v",
            "0": "\0",
            "a": "\a",
            "/": "/",
            "?": "?",
        }
        if nxt in mapping:
            out.append(mapping[nxt])
            i += 2
            continue
        if nxt == "u":
            if i + 5 < n and s[i + 2 : i + 6].isalnum():
                try:
                    out.append(chr(int(s[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(ch)
            i += 1
            continue
        if nxt == "x" and i + 3 < n:
            try:
                out.append(chr(int(s[i + 2 : i + 4], 16)))
                i += 4
                continue
            except ValueError:
                pass
        # Unknown escape: pass through verbatim.
        out.append(ch)
        i += 1
    return "".join(out)


__all__ = ["Parser", "parse_script"]
