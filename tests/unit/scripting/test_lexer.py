"""Unit tests for the scripting lexer."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.scripting.lexer import tokenize

pytestmark = pytest.mark.unit


class TestLexerBasics:
    def test_empty_source(self) -> None:
        tokens = tokenize("")
        assert len(tokens) == 1
        assert tokens[0].kind == "EOF"

    def test_whitespace_only(self) -> None:
        tokens = tokenize("   \n\t  ")
        assert tokens[-1].kind == "EOF"

    def test_keyword_recognition(self) -> None:
        tokens = tokenize("DECLARE x INT64 DEFAULT 1")
        kinds = [t.kind for t in tokens]
        assert kinds[:-1] == ["KEYWORD", "IDENT", "IDENT", "KEYWORD", "NUMBER"]

    def test_case_insensitive_keywords(self) -> None:
        tokens = tokenize("declare x")
        assert tokens[0].kind == "KEYWORD"
        assert tokens[0].value == "DECLARE"

    def test_identifier_with_underscore(self) -> None:
        tokens = tokenize("my_var _private __dunder")
        for t in tokens[:-1]:
            assert t.kind == "IDENT"

    def test_backtick_identifier(self) -> None:
        tokens = tokenize("`hyphen-name`")
        assert tokens[0].kind == "IDENT"
        assert tokens[0].value == "hyphen-name"

    def test_unterminated_backtick(self) -> None:
        with pytest.raises(InvalidQueryError, match="Unterminated backtick"):
            tokenize("`unterminated")

    def test_single_quoted_string(self) -> None:
        tokens = tokenize("'hello world'")
        assert tokens[0].kind == "STRING"
        assert tokens[0].value == "'hello world'"

    def test_double_quoted_string(self) -> None:
        tokens = tokenize('"hello"')
        assert tokens[0].kind == "STRING"

    def test_triple_quoted_string(self) -> None:
        tokens = tokenize("'''triple'''")
        assert tokens[0].kind == "STRING"
        assert tokens[0].value == "'''triple'''"

    def test_string_with_escapes(self) -> None:
        tokens = tokenize(r"'hello\nworld'")
        assert tokens[0].kind == "STRING"

    def test_raw_string(self) -> None:
        tokens = tokenize(r"r'raw\n'")
        assert tokens[0].kind == "STRING"

    def test_unterminated_string(self) -> None:
        with pytest.raises(InvalidQueryError, match="Unterminated string"):
            tokenize("'no end")

    def test_integer_literal(self) -> None:
        tokens = tokenize("123")
        assert tokens[0].kind == "NUMBER"
        assert tokens[0].value == "123"

    def test_float_literal(self) -> None:
        tokens = tokenize("1.5")
        assert tokens[0].kind == "NUMBER"

    def test_scientific_notation(self) -> None:
        tokens = tokenize("1.5e-3")
        assert tokens[0].kind == "NUMBER"
        assert tokens[0].value == "1.5e-3"

    def test_operators(self) -> None:
        tokens = tokenize("+ - * / % = >= <= != <>")
        kinds = [t.kind for t in tokens[:-1]]
        assert all(k == "OP" for k in kinds)

    def test_punctuation(self) -> None:
        tokens = tokenize("() [] {} ,;.")
        kinds = [t.kind for t in tokens[:-1]]
        assert all(k == "PUNCT" for k in kinds)

    def test_at_sign(self) -> None:
        tokens = tokenize("@name @@session")
        assert tokens[0].kind == "AT"
        assert tokens[2].kind == "AT_AT"

    def test_line_comment_dash_dash(self) -> None:
        tokens = tokenize("-- comment\nSELECT")
        # Only SELECT + EOF
        assert tokens[0].kind == "KEYWORD"
        assert tokens[0].value == "SELECT"

    def test_line_comment_hash(self) -> None:
        tokens = tokenize("# hash comment\nSELECT")
        assert tokens[0].kind == "KEYWORD"

    def test_block_comment(self) -> None:
        tokens = tokenize("/* block */ SELECT")
        assert tokens[0].kind == "KEYWORD"

    def test_unterminated_block_comment(self) -> None:
        with pytest.raises(InvalidQueryError, match="Unterminated block comment"):
            tokenize("/* never ends")

    def test_unexpected_character(self) -> None:
        with pytest.raises(InvalidQueryError, match="Unexpected character"):
            tokenize("¢")

    def test_position_tracking(self) -> None:
        tokens = tokenize("DECLARE x")
        assert tokens[0].start == 0
        assert tokens[0].end == 7
        assert tokens[1].start == 8

    def test_decimal_with_leading_dot(self) -> None:
        tokens = tokenize(".5")
        assert tokens[0].kind == "NUMBER"


class TestPositionalParameterMarker:
    """P2.e: the ``?`` BigQuery positional-parameter placeholder lexes as PUNCT."""

    def test_question_mark_is_punct(self) -> None:
        tokens = tokenize("?")
        assert tokens[0].kind == "PUNCT"
        assert tokens[0].value == "?"

    def test_question_mark_in_select(self) -> None:
        """A bare ``?`` survives lexing of a typical positional-param SELECT."""
        tokens = tokenize("SELECT ? AS a, ? AS b")
        # Find the two ? tokens.
        question_marks = [t for t in tokens if t.kind == "PUNCT" and t.value == "?"]
        assert len(question_marks) == 2

    def test_question_mark_in_unnest(self) -> None:
        tokens = tokenize("SELECT v FROM UNNEST(?) AS v")
        question_marks = [t for t in tokens if t.value == "?"]
        assert len(question_marks) == 1
