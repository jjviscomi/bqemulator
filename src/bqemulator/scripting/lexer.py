r"""Tokenizer for BigQuery scripts.

Produces a stream of :class:`Token` instances. The parser uses the
tokens to decide where one statement ends and another begins; raw
text is captured for expressions and pass-through SQL.

Handles:

- Unquoted identifiers and reserved keywords (case-insensitive match).
- Backtick-quoted identifiers (``\\`my name\\```).
- Single- and double-quoted string literals, with ``\\\\`` escapes.
- Triple-quoted strings (``'''...'''`` and triple-double-quote form).
- Raw string prefix (``r'...'``, ``r"..."``, ``r'''...'''``).
- Numeric literals (int + float, including ``1.5e-2``).
- ``--`` line comments and ``/* */`` block comments.
- ``#`` line comments (BigQuery dialect).
- Operators and punctuation: ``(, ), [, ], {, }, ,, ;, ., @, @@, ?``,
  ``=, !=, <>, <, <=, >, >=, ||, +, -, *, /, %``. The ``?`` token is
  BigQuery's positional query-parameter placeholder, captured as a
  PUNCT token so the script parser passes it through to the SQL
  translator unchanged.

The lexer does NOT attempt to tokenise expressions into operator-aware
trees — expressions inside scripting statements are captured as raw
text and delegated to SQLGlot.
"""

from __future__ import annotations

from dataclasses import dataclass

from bqemulator.domain.errors import InvalidQueryError

# BigQuery scripting reserved keywords (upper-case canonical form).
_KEYWORDS: frozenset[str] = frozenset(
    {
        "AS",
        "BEGIN",
        "BREAK",
        "CALL",
        "CONTINUE",
        "CREATE",
        "DECLARE",
        "DEFAULT",
        "DELETE",
        "DO",
        "ELSE",
        "ELSEIF",
        "ELSIF",
        "END",
        "EXCEPTION",
        "EXECUTE",
        "FOR",
        "FROM",
        "FUNCTION",
        "IF",
        "IMMEDIATE",
        "IN",
        "INSERT",
        "INTO",
        "ITERATE",
        "JAVASCRIPT",
        "LANGUAGE",
        "LEAVE",
        "LOOP",
        "MERGE",
        "OR",
        "PROCEDURE",
        "RAISE",
        "REPLACE",
        "RETURN",
        "RETURNS",
        "SELECT",
        "SET",
        "TABLE",
        "TEMP",
        "TEMPORARY",
        "THEN",
        "TRUNCATE",
        "UPDATE",
        "USING",
        "WHEN",
        "WHILE",
    },
)


@dataclass(frozen=True, slots=True)
class Token:
    """A single lexed token."""

    kind: str
    value: str
    start: int
    end: int
    raw_value: str = ""  # original source slice for STRING/IDENT — used for round-tripping


class Lexer:
    """Greedy lexer for BigQuery scripts."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0

    def tokenize(self) -> list[Token]:
        """Consume the entire source and return the token list."""
        tokens: list[Token] = []
        while True:
            token = self._next_token()
            if token.kind == "EOF":
                tokens.append(token)
                return tokens
            tokens.append(token)

    def _next_token(self) -> Token:
        self._skip_whitespace_and_comments()
        if self._pos >= len(self._source):
            return Token(kind="EOF", value="", start=self._pos, end=self._pos)

        start = self._pos
        ch = self._source[self._pos]

        if ch == "`":
            return self._read_backtick_ident(start)
        if ch in ("'", '"'):
            return self._read_string(start)
        if ch.isalpha() or ch == "_":
            # Check for raw-string prefix r'...' or R'...'
            if ch.lower() == "r" and start + 1 < len(self._source):
                nxt = self._source[start + 1]
                if nxt in ("'", '"'):
                    self._pos += 1
                    return self._read_string(start, raw=True)
            return self._read_identifier(start)
        if ch.isdigit() or (ch == "." and self._peek_digit()):
            return self._read_number(start)
        if ch == "@":
            return self._read_at(start)
        return self._read_operator_or_punct(start)

    # -- Whitespace + comments --------------------------------------------

    def _skip_whitespace_and_comments(self) -> None:
        while self._pos < len(self._source):
            ch = self._source[self._pos]
            if ch.isspace():
                self._pos += 1
                continue
            if ch == "-" and self._peek_is("--"):
                self._skip_line_comment()
                continue
            if ch == "#":
                self._skip_line_comment()
                continue
            if ch == "/" and self._peek_is("/*"):
                self._skip_block_comment()
                continue
            break

    def _peek_is(self, text: str) -> bool:
        return self._source.startswith(text, self._pos)

    def _peek_digit(self) -> bool:
        return self._pos + 1 < len(self._source) and self._source[self._pos + 1].isdigit()

    def _skip_line_comment(self) -> None:
        while self._pos < len(self._source) and self._source[self._pos] != "\n":
            self._pos += 1

    def _skip_block_comment(self) -> None:
        self._pos += 2
        while self._pos < len(self._source):
            if self._source.startswith("*/", self._pos):
                self._pos += 2
                return
            self._pos += 1
        raise InvalidQueryError("Unterminated block comment")

    # -- Identifiers -----------------------------------------------------

    def _read_identifier(self, start: int) -> Token:
        while self._pos < len(self._source) and (
            self._source[self._pos].isalnum() or self._source[self._pos] == "_"
        ):
            self._pos += 1
        text = self._source[start : self._pos]
        upper = text.upper()
        if upper in _KEYWORDS:
            return Token(kind="KEYWORD", value=upper, start=start, end=self._pos, raw_value=text)
        return Token(kind="IDENT", value=text, start=start, end=self._pos, raw_value=text)

    def _read_backtick_ident(self, start: int) -> Token:
        self._pos += 1
        while self._pos < len(self._source) and self._source[self._pos] != "`":
            self._pos += 1
        if self._pos >= len(self._source):
            raise InvalidQueryError("Unterminated backtick identifier")
        text = self._source[start + 1 : self._pos]
        self._pos += 1  # consume closing backtick
        return Token(
            kind="IDENT",
            value=text,
            start=start,
            end=self._pos,
            raw_value=self._source[start : self._pos],
        )

    # -- Literals --------------------------------------------------------

    def _read_string(self, start: int, *, raw: bool = False) -> Token:
        quote = self._source[self._pos]
        triple = self._source.startswith(quote * 3, self._pos)
        if triple:
            self._pos += 3
            end_marker = quote * 3
        else:
            self._pos += 1
            end_marker = quote

        while self._pos < len(self._source):
            if not raw and self._source[self._pos] == "\\":
                self._pos += 2  # skip the escape byte
                continue
            if self._source.startswith(end_marker, self._pos):
                self._pos += len(end_marker)
                text = self._source[start : self._pos]
                return Token(kind="STRING", value=text, start=start, end=self._pos, raw_value=text)
            self._pos += 1
        raise InvalidQueryError("Unterminated string literal")

    def _read_number(self, start: int) -> Token:
        has_dot = False
        has_exp = False
        while self._pos < len(self._source):
            ch = self._source[self._pos]
            if ch.isdigit():
                self._pos += 1
                continue
            if ch == "." and not has_dot and not has_exp:
                has_dot = True
                self._pos += 1
                continue
            if ch in ("e", "E") and not has_exp:
                has_exp = True
                self._pos += 1
                if self._pos < len(self._source) and self._source[self._pos] in ("+", "-"):
                    self._pos += 1
                continue
            break
        text = self._source[start : self._pos]
        return Token(kind="NUMBER", value=text, start=start, end=self._pos, raw_value=text)

    def _read_at(self, start: int) -> Token:
        self._pos += 1
        kind = "AT"
        if self._pos < len(self._source) and self._source[self._pos] == "@":
            kind = "AT_AT"
            self._pos += 1
        return Token(
            kind=kind,
            value=self._source[start : self._pos],
            start=start,
            end=self._pos,
        )

    # -- Operators + punctuation -----------------------------------------

    def _read_operator_or_punct(self, start: int) -> Token:
        # Two-character operators that are recognised before their prefixes.
        two = self._source[self._pos : self._pos + 2]
        if two in (">=", "<=", "<>", "!=", "||", "=>", "<<", ">>"):
            self._pos += 2
            return Token(kind="OP", value=two, start=start, end=self._pos)

        ch = self._source[self._pos]
        self._pos += 1
        # ``?`` is BigQuery's positional query-parameter placeholder
        # (``SELECT ? AS a, ? AS b`` bound by ``QueryJobConfig
        # .query_parameters``). The script-statement parser captures
        # SQL pass-through as raw text via the lexer's source slice, so
        # producing a PUNCT token here is sufficient — neither the
        # script parser nor the SQL translator inspect the token kind
        # for ``?``; SQLGlot's BigQuery dialect accepts ``?`` as a
        # placeholder; and the post-translation ``bind_parameters``
        # call replaces the ``?`` with DuckDB's positional marker.
        if ch in "()[]{},;.?":
            return Token(kind="PUNCT", value=ch, start=start, end=self._pos)
        if ch in "+-*/%<>=|&^~!":
            return Token(kind="OP", value=ch, start=start, end=self._pos)
        raise InvalidQueryError(f"Unexpected character {ch!r} at offset {start}")


def tokenize(source: str) -> list[Token]:
    """Tokenize ``source`` into a list of tokens terminated by EOF."""
    return Lexer(source).tokenize()


__all__ = ["Lexer", "Token", "tokenize"]
