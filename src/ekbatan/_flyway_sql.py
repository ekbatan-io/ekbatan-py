"""Reading a migration file as Flyway reads it: the checksum, the placeholders, the statements.

Java's migrator runs Flyway 12, and a Python service shares its databases, so a file has to mean
the same thing to both: the same checksum in ``flyway_schema_history``, the same placeholders
replaced, the same statements sent, the same decision about running outside a transaction. Each
piece below is a port of the Flyway class named in its docstring, kept close to the original so
the two can be read side by side; where Flyway's behaviour is odd, the oddity is kept, because a
file that splits differently in the two ports is worse than either way of splitting it.

Nothing here touches a database; the dialect's server version is handed in where Flyway asks
the database (PostgreSQL's ``ALTER TYPE ... ADD VALUE``).
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto

#: Flyway's page on what its parser cannot do, which it names in every parse error.
KNOWN_PARSER_LIMITATIONS = "https://rd.gt/3ipi7Pm"


class SqlParseError(Exception):
    """A file Flyway would refuse to parse: an unknown placeholder, an unfinished statement."""


# -- the checksum ------------------------------------------------------------------------------


_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def java_lines(text: str) -> list[str]:
    """The lines ``java.io.BufferedReader.readLine`` returns: split at ``\\n``, ``\\r`` or
    ``\\r\\n``, with no empty line after a final line break. ``str.splitlines`` splits at more
    characters than Java does, so it cannot be used."""
    lines = _LINE_BREAK.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def checksum(text: str) -> int:
    """Flyway's ``ChecksumCalculator``: a CRC32 over the UTF-8 bytes of each line, without its
    line break, the byte-order mark taken off the first; as a signed 32-bit number, as Java's
    ``(int) crc32.getValue()`` stores it.

    So a file whose only change is its line breaks has the same checksum - and so does one where
    two lines were joined into one, which Flyway does not see as an edit either.
    """
    crc = 0
    for index, line in enumerate(java_lines(text)):
        if index == 0:
            line = line.removeprefix("\ufeff")
        crc = zlib.crc32(line.encode("utf-8", "surrogatepass"), crc)
    return crc - (1 << 32) if crc >= (1 << 31) else crc


def java_string_hash(text: str) -> int:
    """Java's ``String.hashCode``: over UTF-16 code units, wrapped to a signed 32-bit int. Flyway
    builds its lock's key and name from it."""
    value = 0
    data = text.encode("utf-16-be", "surrogatepass")
    for i in range(0, len(data), 2):
        value = (31 * value + ((data[i] << 8) | data[i + 1])) & 0xFFFFFFFF
    return value - (1 << 32) if value >= (1 << 31) else value


# -- the placeholders --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Placeholders:
    """What replaces ``${name}`` in a file - Flyway's placeholder settings."""

    values: Mapping[str, str | None] = field(default_factory=dict)
    prefix: str = "${"
    suffix: str = "}"
    separator: str = ":"


def replace_placeholders(text: str, placeholders: Placeholders) -> str:
    """Flyway's ``PlaceholderReplacingReader``, character for character.

    Names are matched ignoring case. A name with no value fails, as Flyway fails - with its own
    wording for one of the built-in ``flyway:`` names. The reader's quirks are kept: it looks for
    the prefix only at a character it has not already looked at, so ``$${x}`` is left alone, and
    a prefix with no suffix after it swallows the rest of the file.
    """
    prefix, suffix = placeholders.prefix, placeholders.suffix
    values = {key.lower(): value for key, value in placeholders.values.items()}
    out: list[str] = []
    buffer = ""
    position, end = 0, len(text)
    while True:
        if buffer:
            out.append(buffer[0])
            buffer = buffer[1:]
            continue
        # Read ahead by the prefix's length, for as long as what was read could start it.
        while True:
            if position >= end:
                break
            buffer += text[position]
            position += 1
            if not (len(buffer) < len(prefix) and buffer == prefix[: len(buffer)]):
                break
        if not buffer.endswith(prefix):
            if buffer:
                out.append(buffer[0])
                buffer = buffer[1:]
                continue
            return "".join(out)
        buffer = ""
        name = ""
        while True:
            if position >= end:
                break
            name += text[position]
            position += 1
            if name.endswith(suffix):
                break
        name = name[: max(0, len(name) - len(suffix))]
        if name.lower() not in values:
            canonical = prefix + name + suffix
            if name.startswith("flyway" + placeholders.separator):
                raise SqlParseError(
                    f"Failed to populate value for default placeholder: {canonical}"
                )
            raise SqlParseError(
                f"No value provided for placeholder: {canonical}.  Check your configuration!"
            )
        out.append(values[name.lower()] or "")


# -- the statements ----------------------------------------------------------------------------


class TokenType(Enum):
    EOF = auto()
    STRING = auto()
    IDENTIFIER = auto()
    COMMENT = auto()
    MULTI_LINE_COMMENT_DIRECTIVE = auto()
    NUMERIC = auto()
    DELIMITER = auto()
    NEW_DELIMITER = auto()
    KEYWORD = auto()
    SYMBOL = auto()
    BLANK_LINES = auto()


@dataclass(frozen=True, slots=True)
class Token:
    type: TokenType
    pos: int
    line: int
    col: int
    text: str | None
    parens_depth: int


class StatementType(Enum):
    UNKNOWN = auto()
    GENERIC = auto()
    COPY = auto()
    STORED_PROGRAM = auto()
    BEGIN_NOT_ATOMIC = auto()


@dataclass(frozen=True, slots=True)
class Statement:
    """One statement as Flyway sends it: without its delimiter, trimmed."""

    sql: str
    line: int
    col: int
    #: What Flyway's parser decided: ``False`` for a statement PostgreSQL refuses inside a
    #: transaction - ``CREATE INDEX CONCURRENTLY``, ``VACUUM`` and the like.
    can_execute_in_transaction: bool = True
    #: The rows of a PostgreSQL ``COPY ... FROM STDIN``, which follow the statement in the file.
    copy_data: str | None = None


def _java_whitespace(c: str) -> bool:
    """``Character.isWhitespace``: no-break spaces are not whitespace to Java."""
    return c.isspace() and c not in "\u00a0\u2007\u202f"


class _Reader:
    """Flyway's ``PeekingReader``, ``PositionTracker`` and ``Recorder`` over one string."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.line = 1
        self.col = 1
        self.recording_from: int | None = None
        self.confirmed = 0

    # -- reading
    def read(self) -> str | None:
        if self.pos >= len(self.text):
            self.pos += 1  # Java counts a read past the end too; harmless
            return None
        c = self.text[self.pos]
        self.pos += 1
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return c

    def swallow(self, n: int = 1) -> None:
        for _ in range(n):
            self.read()

    def peek(self, n: int) -> str | None:
        """Up to ``n`` characters, never past the first line break - as Flyway peeks."""
        chunk = self.text[self.pos : self.pos + n]
        newline = chunk.find("\n")
        if newline >= 0:
            chunk = chunk[: newline + 1]
        return chunk or None

    def peek_is(self, s: str) -> bool:
        return self.peek(len(s)) == s

    def peek_char(self, c: str) -> bool:
        return self.pos < len(self.text) and self.text[self.pos] == c

    def read_until_excluding_chars(self, c1: str, c2: str) -> str:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in (c1, c2):
            self.read()
        return self.text[start : self.pos]

    def read_until_excluding(self, *strings: str) -> str:
        out: list[str] = []
        while True:
            for s in strings:
                if self.peek_is(s):
                    return "".join(out)
            c = self.read()
            if c is None:
                return "".join(out)
            out.append(c)

    def swallow_until_excluding(self, s: str) -> None:
        while not self.peek_is(s):
            if self.read() is None:
                return

    def read_until_including(self, delimiter: str) -> str:
        out = []
        while True:
            c = self.read()
            if c is None:
                break
            out.append(c)
            if c == delimiter:
                break
        return "".join(out)

    def swallow_until_including_with_escape(
        self, delimiter: str, self_escape: bool, escape: str = ""
    ) -> None:
        while True:
            c = self.read()
            if c is None:
                return
            if escape and c == escape:
                self.swallow()
                continue
            if c == delimiter:
                if self_escape and self.peek_char(delimiter):
                    self.swallow()
                    continue
                return

    def read_until_excluding_with_escape(self, delimiter: str, self_escape: bool) -> str:
        out = []
        while True:
            c = self.read()
            if c is None:
                break
            if c == delimiter:
                if self_escape and self.peek_char(delimiter):
                    out.append(delimiter * 2)
                    self.read()
                    continue
                break
            out.append(c)
        return "".join(out)

    def read_numeric(self) -> str:
        start = self.pos
        while self.pos < len(self.text) and "0" <= self.text[self.pos] <= "9":
            self.read()
        return self.text[start : self.pos]

    def read_whitespace(self) -> str:
        start = self.pos
        while self.pos < len(self.text) and _java_whitespace(self.text[self.pos]):
            self.read()
        return self.text[start : self.pos]

    # -- recording
    def start(self) -> None:
        self.recording_from = self.pos
        self.confirmed = self.pos

    def confirm(self) -> None:
        self.confirmed = min(self.pos, len(self.text))

    def stop(self) -> str:
        start = self.recording_from if self.recording_from is not None else self.pos
        self.recording_from = None
        return self.text[start : self.confirmed]


class _Context:
    """Flyway's ``ParserContext``: depths, the delimiter, the statement's type."""

    def __init__(self, delimiter: str) -> None:
        self.parens_depth = 0
        self.block_depth = 0
        self.block_initiators: list[str] = []
        self.delimiter = delimiter
        self.statement_type = StatementType.UNKNOWN

    def increase_block_depth(self, initiator: str) -> None:
        self.block_initiators.append(initiator)
        self.block_depth += 1

    def decrease_block_depth(self) -> None:
        if self.block_depth == 0:
            raise SqlParseError("Parsing failed: unable to decrease block depth below 0")
        self.block_depth -= 1
        self.block_initiators.pop()

    @property
    def block_initiator(self) -> str:
        return self.block_initiators[-1] if self.block_initiators else ""


def _previous_token(tokens: list[Token], parens_depth: int) -> Token | None:
    for token in reversed(tokens):
        if token.parens_depth < parens_depth:
            break
        if token.parens_depth != parens_depth:
            continue
        if token.type in (TokenType.COMMENT, TokenType.BLANK_LINES):
            continue
        return token
    return None


def _last_token_is(tokens: list[Token], parens_depth: int, text: str) -> bool:
    previous = _previous_token(tokens, parens_depth)
    return previous is not None and previous.text == text


class Parser:
    """Flyway's ``Parser``: the base every dialect's parser derives from."""

    peek_depth = 3
    identifier_quote = '"'
    alternative_identifier_quote = ""
    alternative_string_literal_quote = ""
    default_delimiter = ";"
    transactional_detection_cutoff = 10

    def __init__(self, server_version: tuple[int, ...] | None = None) -> None:
        self.server_version = server_version

    def parse(self, text: str) -> list[Statement]:
        """Every statement in ``text``, which has had its placeholders replaced already."""
        reader = _Reader(text.removeprefix("\ufeff"))
        context = _Context(self.default_delimiter)
        statements: list[Statement] = []
        while True:
            statement = self._next_statement(reader, context)
            if statement is None:
                return statements
            statements.append(statement)

    # -- the hooks a dialect overrides ---------------------------------------------------------

    def reset_delimiter(self, context: _Context) -> None:
        context.delimiter = self.default_delimiter

    def should_adjust_block_depth(
        self, context: _Context, tokens: list[Token], token: Token
    ) -> bool:
        return token.type is TokenType.KEYWORD and token.parens_depth == 0

    def adjust_block_depth(self, context: _Context, tokens: list[Token], keyword: Token) -> None:
        pass

    def detect_statement_type(self, simplified: str) -> StatementType:
        return StatementType.UNKNOWN

    def detect_can_execute_in_transaction(self, simplified: str) -> bool | None:
        return True

    def is_single_line_comment(self, peek: str, context: _Context) -> bool:
        return peek.startswith("--")

    def is_comment_directive(self, peek: str) -> bool:
        return False

    def handle_comment_directive(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        raise NotImplementedError

    def handle_string_literal(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        reader.swallow()
        reader.swallow_until_including_with_escape("'", True)
        return Token(TokenType.STRING, *at, None, context.parens_depth)

    def handle_alternative_string_literal(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        raise NotImplementedError

    def handle_keyword(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int], keyword: str
    ) -> Token:
        return Token(TokenType.KEYWORD, *at, keyword.upper(), context.parens_depth)

    def read_copy_data(self, reader: _Reader) -> str:
        raise NotImplementedError

    # -- the loop ------------------------------------------------------------------------------

    def _next_statement(self, reader: _Reader, context: _Context) -> Statement | None:
        self.reset_delimiter(context)
        context.statement_type = StatementType.UNKNOWN
        statement_line, statement_col = reader.line, reader.col
        try:
            return self._read_statement(reader, context)
        except SqlParseError as error:
            raise SqlParseError(
                f"Unable to parse statement at line {statement_line} col {statement_col}. See "
                f"{KNOWN_PARSER_LIMITATIONS} for more information. {error}"
            ) from None

    def _read_statement(self, reader: _Reader, context: _Context) -> Statement | None:
        tokens: list[Token] = []
        keywords: list[Token] = []
        reader.start()
        statement_line, statement_col = reader.line, reader.col
        non_comment_part = False
        statement_type = StatementType.UNKNOWN
        can_execute: bool | None = None
        simplified = ""
        while True:
            token = self._read_token(reader, context)
            if token is None:
                if not tokens:
                    reader.start()
                    statement_line, statement_col = reader.line, reader.col
                    simplified = ""
                else:
                    reader.confirm()
                continue
            kind = token.type
            if kind is TokenType.NEW_DELIMITER:
                if tokens and non_comment_part:
                    raise SqlParseError(
                        f"Delimiter changed inside statement at line {statement_line} col "
                        f"{statement_col}: {reader.stop()}"
                    )
                context.delimiter = token.text or context.delimiter
                tokens.clear()
                reader.start()
                statement_line, statement_col = reader.line, reader.col
                simplified = ""
                continue
            if kind is TokenType.DELIMITER and not non_comment_part:
                tokens.clear()
                reader.start()
                statement_line, statement_col = reader.line, reader.col
                simplified = ""
                continue
            if self.should_adjust_block_depth(context, tokens, token):
                if kind is TokenType.KEYWORD:
                    keywords.append(token)
                self.adjust_block_depth(context, tokens, token)
            parens_depth = token.parens_depth
            block_depth = context.block_depth
            if kind is TokenType.EOF or (
                kind is TokenType.DELIMITER and parens_depth == 0 and block_depth == 0
            ):
                sql = reader.stop()
                if kind is TokenType.EOF and (
                    not sql.strip() or not tokens or not non_comment_part
                ):
                    return None
                if can_execute is None:
                    can_execute = self._determine_can_execute(simplified, keywords, True)
                if kind is TokenType.EOF and (parens_depth > 0 or block_depth > 0):
                    raise SqlParseError(
                        f"Incomplete statement at line {statement_line} col {statement_col}: {sql}"
                    )
                copy_data = None
                if statement_type is StatementType.COPY:
                    copy_data = self.read_copy_data(reader)
                return Statement(
                    sql.strip(), statement_line, statement_col, bool(can_execute), copy_data
                )
            if not tokens or all(
                t.type in (TokenType.BLANK_LINES, TokenType.COMMENT) for t in tokens
            ):
                non_comment_part = False
                statement_line, statement_col = token.line, token.col
            tokens.append(token)
            reader.confirm()
            if not non_comment_part and kind not in (
                TokenType.COMMENT,
                TokenType.DELIMITER,
                TokenType.BLANK_LINES,
            ):
                non_comment_part = True
            if (
                len(keywords) <= self.transactional_detection_cutoff
                and (kind is TokenType.KEYWORD or token.text == "@")
                and parens_depth == 0
                and (statement_type is StatementType.UNKNOWN or can_execute is None)
            ):
                if simplified:
                    simplified += " "
                simplified += (token.text or "").upper()
                if statement_type is StatementType.UNKNOWN:
                    if len(keywords) > self.transactional_detection_cutoff:
                        statement_type = StatementType.GENERIC
                    else:
                        statement_type = self.detect_statement_type(simplified)
                        context.statement_type = statement_type
                if can_execute is None:
                    can_execute = self._determine_can_execute(simplified, keywords, None)

    def _determine_can_execute(
        self, simplified: str, keywords: list[Token], default: bool | None
    ) -> bool | None:
        if len(keywords) > self.transactional_detection_cutoff:
            return True
        detected = self.detect_can_execute_in_transaction(simplified)
        return default if detected is None else detected

    def _read_token(self, reader: _Reader, context: _Context) -> Token | None:
        at = (reader.pos, reader.line, reader.col)
        peek = reader.peek(self.peek_depth)
        if peek is None:
            return Token(TokenType.EOF, *at, None, 0)
        c = peek[0]
        if self.alternative_string_literal_quote and c == self.alternative_string_literal_quote:
            return self.handle_alternative_string_literal(reader, context, at)
        if c == "'":
            return self.handle_string_literal(reader, context, at)
        if c == "(":
            context.parens_depth += 1
            reader.swallow()
            return None
        if c == ")":
            context.parens_depth -= 1
            reader.swallow()
            return None
        if c == self.identifier_quote or (
            self.alternative_identifier_quote and c == self.alternative_identifier_quote
        ):
            reader.swallow()
            text = reader.read_until_excluding_with_escape(c, True)
            if reader.peek_char("."):
                text = self._read_additional_identifier_parts(reader, c, context)
            return Token(TokenType.IDENTIFIER, *at, text, context.parens_depth)
        if self.is_comment_directive(peek):
            return self.handle_comment_directive(reader, context, at)
        if self.is_single_line_comment(peek, context):
            text = reader.read_until_excluding_chars("\n", "\r")
            return Token(TokenType.COMMENT, *at, text, context.parens_depth)
        if peek.startswith("/*"):
            return self._handle_multiline_comment(reader, context, at)
        if "0" <= c <= "9":
            text = reader.read_numeric()
            return Token(TokenType.NUMERIC, *at, text, context.parens_depth)
        if peek.startswith(("B'", "E'", "X'")):
            reader.swallow(2)
            reader.swallow_until_including_with_escape("'", True, "\\")
            return Token(TokenType.STRING, *at, None, context.parens_depth)
        if peek.startswith("U&'"):
            reader.swallow(3)
            reader.swallow_until_including_with_escape("'", True)
            return Token(TokenType.STRING, *at, None, context.parens_depth)
        if peek.startswith(context.delimiter):
            reader.swallow(len(context.delimiter))
            return Token(TokenType.DELIMITER, *at, context.delimiter, context.parens_depth)
        if c == "_" or c.isalpha():
            text = c + self._read_keyword_part(reader, context, first=True)
            if reader.peek_char("."):
                text += self._read_additional_identifier_parts(
                    reader, self.identifier_quote, context
                )
            if not self._is_keyword(text):
                return Token(TokenType.IDENTIFIER, *at, text, context.parens_depth)
            return self.handle_keyword(reader, context, at, text)
        if c in (" ", "\r", "\u00a0"):
            reader.swallow()
            return None
        if _java_whitespace(c):
            text = reader.read_whitespace()
            if text.count("\n") >= 2:
                return Token(TokenType.BLANK_LINES, *at, text, context.parens_depth)
            return None
        reader.read()
        return Token(TokenType.SYMBOL, *at, c, context.parens_depth)

    def _read_keyword_part(self, reader: _Reader, context: _Context, first: bool = False) -> str:
        if first:
            reader.read()
        out = []
        while reader.pos < len(reader.text):
            if reader.peek_is(context.delimiter):
                break
            c = reader.text[reader.pos]
            if c in "_$" or c.isalnum():
                out.append(c)
                reader.read()
            else:
                break
        return "".join(out)

    @staticmethod
    def _is_keyword(text: str) -> bool:
        return all(c.isalpha() or c == "_" for c in text)

    def _read_additional_identifier_parts(
        self, reader: _Reader, quote: str, context: _Context
    ) -> str:
        result = ""
        for _ in range(2):
            reader.swallow()
            result += "."
            if reader.peek_char(quote):
                reader.swallow()
                result += reader.read_until_excluding_with_escape(quote, True)
            else:
                result += self._read_keyword_part(reader, context)
            if not reader.peek_char("."):
                break
        return result

    def _handle_multiline_comment(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        depth = 0
        reader.swallow(2)
        text = reader.read_until_excluding("*/", "/*")
        while reader.peek_is("/*") or depth > 0:
            if reader.peek_is("/*"):
                depth += 1
            else:
                depth -= 1
            if reader.pos >= len(reader.text):
                break
            reader.swallow(2)
            text += reader.read_until_excluding("*/", "/*")
        reader.swallow(2)
        return Token(TokenType.COMMENT, *at, text, context.parens_depth)


class PostgreSQLParser(Parser):
    """Flyway's ``PostgreSQLParser``."""

    peek_depth = 3
    alternative_string_literal_quote = "$"

    COPY_FROM_STDIN = re.compile(r"^COPY( .*)? FROM STDIN")
    NOT_IN_A_TRANSACTION = (
        re.compile(r"^(CREATE|DROP) (DATABASE|TABLESPACE|SUBSCRIPTION)"),
        re.compile(r"^ALTER SYSTEM"),
        re.compile(r"^(CREATE|DROP)( UNIQUE)? INDEX CONCURRENTLY"),
        re.compile(r"^REINDEX( VERBOSE)? (SCHEMA|DATABASE|SYSTEM)"),
        re.compile(r"^VACUUM"),
        re.compile(r"^DISCARD ALL"),
    )
    ALTER_TYPE_ADD_VALUE = re.compile(r"^ALTER TYPE( .*)? ADD VALUE")

    def adjust_block_depth(self, context: _Context, tokens: list[Token], keyword: Token) -> None:
        text = (keyword.text or "").upper()
        if _last_token_is(tokens, context.parens_depth, "BEGIN") and text == "ATOMIC":
            context.increase_block_depth("ATOMIC")
        if text == "CASE" and context.block_initiator == "ATOMIC":
            context.increase_block_depth("CASE")
        if (
            context.block_depth > 0
            and text == "END"
            and context.block_initiator in ("ATOMIC", "CASE")
        ):
            context.decrease_block_depth()

    def detect_statement_type(self, simplified: str) -> StatementType:
        if self.COPY_FROM_STDIN.fullmatch(simplified):
            return StatementType.COPY
        return StatementType.UNKNOWN

    def detect_can_execute_in_transaction(self, simplified: str) -> bool | None:
        if any(pattern.fullmatch(simplified) for pattern in self.NOT_IN_A_TRANSACTION):
            return False
        # Flyway assumes a server older than 12 when it cannot tell, as this does.
        under_12 = self.server_version is None or self.server_version < (12,)
        if under_12 and self.ALTER_TYPE_ADD_VALUE.fullmatch(simplified):
            return False
        return None

    def handle_alternative_string_literal(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        # $tag$ ... $tag$ - a dollar-quoted string, the tag possibly empty.
        dollar_quote = (reader.read() or "") + reader.read_until_including("$")
        reader.swallow_until_excluding(dollar_quote)
        reader.swallow(len(dollar_quote))
        return Token(TokenType.STRING, *at, None, context.parens_depth)

    def read_copy_data(self, reader: _Reader) -> str:
        reader.read_until_including("\n")
        lines = []
        while True:
            line = reader.read_until_including("\n")
            if line.strip() == "\\." or not line:
                break
            lines.append(line)
        return "".join(lines)


class MySQLParser(Parser):
    """Flyway's ``MySQLParser``: backticks, ``#`` comments, ``DELIMITER``, ``BEGIN ... END``."""

    peek_depth = 8
    identifier_quote = "`"
    alternative_string_literal_quote = '"'

    STORED_PROGRAM = re.compile(
        r"^CREATE\s(((DEFINER\s(\w+\s)?@\s(\w+\s)?)?(PROCEDURE|FUNCTION|EVENT))|TRIGGER)",
        re.IGNORECASE,
    )

    def reset_delimiter(self, context: _Context) -> None:
        # A DELIMITER outlives the statement it came before, as in the mysql client.
        pass

    def handle_keyword(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int], keyword: str
    ) -> Token:
        if keyword.upper() == "DELIMITER":
            text = ""
            while not text:
                text = reader.read_until_excluding_chars("\n", "\r").strip()
                reader.swallow(1)
                if reader.pos > len(reader.text) and not text:
                    raise SqlParseError("DELIMITER with nothing after it")
            return Token(TokenType.NEW_DELIMITER, *at, text, context.parens_depth)
        return super().handle_keyword(reader, context, at, keyword)

    def is_single_line_comment(self, peek: str, context: _Context) -> bool:
        return peek.startswith("--") or (peek[0] == "#" and not peek.startswith(context.delimiter))

    def handle_string_literal(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        reader.swallow()
        reader.swallow_until_including_with_escape("'", True, "\\")
        return Token(TokenType.STRING, *at, None, context.parens_depth)

    def handle_alternative_string_literal(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        reader.swallow()
        reader.swallow_until_including_with_escape('"', True, "\\")
        return Token(TokenType.STRING, *at, None, context.parens_depth)

    def is_comment_directive(self, peek: str) -> bool:
        return len(peek) >= 8 and peek.startswith("/*!") and peek[3:8].isdigit()

    def handle_comment_directive(
        self, reader: _Reader, context: _Context, at: tuple[int, int, int]
    ) -> Token:
        reader.swallow(2)
        text = reader.read_until_excluding("*/")
        reader.swallow(2)
        return Token(TokenType.MULTI_LINE_COMMENT_DIRECTIVE, *at, text, context.parens_depth)

    def detect_statement_type(self, simplified: str) -> StatementType:
        if self.STORED_PROGRAM.fullmatch(simplified):
            return StatementType.STORED_PROGRAM
        return StatementType.UNKNOWN

    def should_adjust_block_depth(
        self, context: _Context, tokens: list[Token], token: Token
    ) -> bool:
        # Any token outside parentheses, not only keywords: an END is closed by what follows it.
        return token.parens_depth == 0

    def adjust_block_depth(self, context: _Context, tokens: list[Token], keyword: Token) -> None:
        text = (keyword.text or "").upper()
        parens_depth = keyword.parens_depth
        if text == "BEGIN" and context.statement_type is StatementType.STORED_PROGRAM:
            context.increase_block_depth("")
        if text == "CASE" and not _last_token_is(tokens, parens_depth, "END"):
            context.increase_block_depth("")
        if (
            context.block_depth > 0
            and _last_token_is(tokens, parens_depth, "END")
            and text not in ("IF", "LOOP", "REPEAT", "WHILE")
        ):
            context.decrease_block_depth()


class MariaDBParser(MySQLParser):
    """Flyway's ``MariaDBParser``: MySQL's, and ``BEGIN NOT ATOMIC ... END``."""

    BEGIN_NOT_ATOMIC = re.compile(r"^BEGIN\sNOT\sATOMIC\s.*END", re.IGNORECASE)

    def detect_statement_type(self, simplified: str) -> StatementType:
        if self.BEGIN_NOT_ATOMIC.fullmatch(simplified):
            return StatementType.BEGIN_NOT_ATOMIC
        return super().detect_statement_type(simplified)

    def adjust_block_depth(self, context: _Context, tokens: list[Token], keyword: Token) -> None:
        text = (keyword.text or "").upper()
        if _last_token_is(tokens, context.parens_depth, "NOT") and text == "ATOMIC":
            context.increase_block_depth("")
        if (
            context.block_depth > 0
            and context.statement_type is StatementType.BEGIN_NOT_ATOMIC
            and text == "END"
        ):
            context.decrease_block_depth()
        super().adjust_block_depth(context, tokens, keyword)


class SQLiteParser(Parser):
    """Flyway's ``SQLiteParser``."""

    peek_depth = 3
    alternative_identifier_quote = "`"

    def detect_can_execute_in_transaction(self, simplified: str) -> bool | None:
        if simplified == "PRAGMA FOREIGN_KEYS":
            return False
        return None

    def adjust_block_depth(self, context: _Context, tokens: list[Token], keyword: Token) -> None:
        text = keyword.text
        if text in ("BEGIN", "CASE"):
            context.increase_block_depth(text)
        elif text == "END":
            context.decrease_block_depth()


def parser_for(dialect: str, server_version: tuple[int, ...] | None = None) -> Parser:
    """The parser Flyway picks for this database."""
    parsers: dict[str, type[Parser]] = {
        "postgresql": PostgreSQLParser,
        "mysql": MySQLParser,
        "mariadb": MariaDBParser,
        "sqlite": SQLiteParser,
    }
    return parsers[dialect](server_version)
