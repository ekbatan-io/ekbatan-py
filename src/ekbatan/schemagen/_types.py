"""The type table: a reflected column type, written as Python that imports and means the same.

Two rules, in order:

1. **A generic SQLAlchemy type where it is faithful** - ``INTEGER()``, ``VARCHAR(length=36)``,
   ``NUMERIC(precision=19, scale=4)``, ``BOOLEAN()``, ``TEXT()``, ``DATE()``. These are importable
   from ``sqlalchemy`` and build the same column on the database the schema was read from.
2. **The dialect's own type where no generic one says the same** - ``mysql.TINYINT(unsigned=True)``,
   ``mysql.LONGTEXT()``, ``postgresql.INET()``, ``postgresql.INTERVAL(fields='day to second')`` -
   imported from its dialect module, never from ``sqlalchemy``, which does not have it.

What reflection reports but nobody declared is dropped: MariaDB's display widths
(``BIGINT(20)``), the ``precision=53`` PostgreSQL puts on every double. What a column holds is
kept: length, precision, scale, ``unsigned``, fractional seconds, collation, enum values, an
array's item type.

A type SQLAlchemy cannot read at all (PostgreSQL ``xml``, MariaDB ``INET6``, pgvector's
``vector(3)``) is written as the database spells it, ``sql_type("xml")``: ``create_all`` builds
the same column, and values come as the driver gives them. A converter turns them into a class
of your own.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from sqlalchemy import types as sqltypes
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeEngine

from ..errors import ValidationError
from ._config import ForcedType

#: The framework's column helpers a forced type may name.
HELPERS = frozenset({"uuid_column", "json_column", "timestamp_column"})

#: The dialect modules a generated schema may use, by the name it imports them under.
DIALECT_MODULES = ("mysql", "postgresql", "sqlite")


@dataclass(frozen=True)
class Emitted:
    """A column type as source, and the imports that source needs beyond SQLAlchemy's own.

    Names from ``sqlalchemy`` and the dialect modules are found in the source itself when the
    module is written; ``imports`` carries only the rest - a framework helper, your own type.
    """

    source: str
    imports: frozenset[tuple[str, str]] = field(default_factory=frozenset)


def _sa(name: str, args: str = "") -> Emitted:
    return Emitted(f"{name}({args})")


def _of(dialect: str, name: str, args: str = "") -> Emitted:
    return Emitted(f"{dialect}.{name}({args})")


#: Characters a string literal cannot hold as they are: a ``\r`` ends the line, and so the
#: literal, and a NUL is refused in source at all. Tab is the one control character left as is.
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def lit(value: Any) -> str:
    """A literal as the generated module writes it: strings double-quoted, the rest as repr."""
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + _CONTROL.sub(_escape, escaped) + '"'
    return repr(value)


def _escape(found: re.Match[str]) -> str:
    character = found.group()
    return {"\n": "\\n", "\r": "\\r"}.get(character, f"\\x{ord(character):02x}")


def _kw(**values: Any) -> str:
    return ", ".join(f"{k}={lit(v)}" for k, v in values.items() if v is not None and v is not False)


def emit_type(reflected: TypeEngine[Any], dialect: str | None) -> Emitted | None:
    """The source for a reflected type, or ``None`` when there is no faithful one."""
    from sqlalchemy.dialects import mysql, postgresql
    from sqlalchemy.dialects.mysql import types as mysql_types

    t = reflected
    if isinstance(t, sqltypes.NullType):
        return None

    if isinstance(t, sqltypes.ARRAY):
        item = emit_type(t.item_type, dialect)
        if item is None:
            return None
        dims = f", dimensions={t.dimensions}" if t.dimensions else ""
        return Emitted(f"postgresql.ARRAY({item.source}{dims})", item.imports)

    if isinstance(t, sqltypes.Enum):
        values = ", ".join(lit(v) for v in t.enums)
        extra = _kw(name=getattr(t, "name", None), schema=getattr(t, "schema", None))
        return _sa("Enum", ", ".join(x for x in (values, extra) if x))

    if isinstance(t, mysql.SET):
        return _of("mysql", "SET", ", ".join(lit(v) for v in t.values))

    if isinstance(t, sqltypes.JSON):
        if isinstance(t, postgresql.JSONB):
            return _of("postgresql", "JSONB", "none_as_null=True")
        return _sa("JSON", "none_as_null=True")

    if isinstance(t, sqltypes.Boolean):
        return _sa("BOOLEAN")

    if isinstance(t, mysql_types._IntegerType):
        name = type(t).__name__
        unsigned, zerofill = bool(t.unsigned), bool(t.zerofill)
        # MySQL's BOOLEAN is TINYINT(1), and the display width is the only trace of it.
        if name == "TINYINT" and t.display_width == 1 and not unsigned and not zerofill:
            return _sa("BOOLEAN")
        if unsigned or zerofill or name in ("TINYINT", "MEDIUMINT"):
            return _of("mysql", name, _kw(unsigned=unsigned, zerofill=zerofill))
        return _sa(name)

    if isinstance(t, mysql.YEAR):
        return _of("mysql", "YEAR")
    if isinstance(t, mysql.BIT):
        return _of("mysql", "BIT", _kw(length=t.length))

    if isinstance(t, sqltypes.Float):
        if isinstance(t, mysql_types._FloatType) and (t.unsigned or t.zerofill or t.scale):
            return _of(
                "mysql",
                type(t).__name__,
                _kw(precision=t.precision, scale=t.scale, unsigned=t.unsigned, zerofill=t.zerofill),
            )
        # Generic, which reads a float: MySQL's own DOUBLE reflects as `asdecimal=True` and
        # would hand back a Decimal, where Java reads a Double.
        if isinstance(t, sqltypes.DOUBLE_PRECISION):
            return _sa("DOUBLE_PRECISION")
        if isinstance(t, sqltypes.REAL):
            return _sa("REAL")
        if isinstance(t, sqltypes.Double):
            return _sa("DOUBLE")
        return _sa("FLOAT")

    if isinstance(t, sqltypes.Numeric):
        name = "DECIMAL" if isinstance(t, sqltypes.DECIMAL) else "NUMERIC"
        if isinstance(t, mysql_types._NumericType) and (t.unsigned or t.zerofill):
            return _of(
                "mysql",
                name,
                _kw(precision=t.precision, scale=t.scale, unsigned=t.unsigned, zerofill=t.zerofill),
            )
        return _sa(name, _kw(precision=t.precision, scale=t.scale))

    if isinstance(t, sqltypes.BigInteger):
        return _sa("BIGINT")
    if isinstance(t, sqltypes.SmallInteger):
        return _sa("SMALLINT")
    if isinstance(t, sqltypes.Integer):
        return _sa("INTEGER")

    if isinstance(t, sqltypes.DateTime):
        name = "TIMESTAMP" if isinstance(t, sqltypes.TIMESTAMP) else "DATETIME"
        if isinstance(t, (mysql.DATETIME, mysql.TIMESTAMP)) and t.fsp:
            return _of("mysql", name, _kw(fsp=t.fsp))
        if isinstance(t, postgresql.TIMESTAMP):
            if t.precision is not None:
                return _of(
                    "postgresql", "TIMESTAMP", _kw(timezone=t.timezone, precision=t.precision)
                )
            return _sa("TIMESTAMP", _kw(timezone=t.timezone))
        return _sa(name, _kw(timezone=t.timezone))

    if isinstance(t, sqltypes.Time):
        if isinstance(t, mysql.TIME) and t.fsp:
            return _of("mysql", "TIME", _kw(fsp=t.fsp))
        if isinstance(t, postgresql.TIME) and t.precision is not None:
            return _of("postgresql", "TIME", _kw(timezone=t.timezone, precision=t.precision))
        return _sa("TIME", _kw(timezone=t.timezone))

    if isinstance(t, sqltypes.Date):
        return _sa("DATE")

    if isinstance(t, postgresql.INTERVAL):
        return _of("postgresql", "INTERVAL", _kw(precision=t.precision, fields=t.fields))

    if isinstance(t, sqltypes.String):
        return _string(t)

    if isinstance(t, postgresql.BYTEA):
        return _sa("LargeBinary")
    if isinstance(t, (mysql.TINYBLOB, mysql.MEDIUMBLOB, mysql.LONGBLOB)):
        return _of("mysql", type(t).__name__)
    if isinstance(t, (sqltypes.BINARY, sqltypes.VARBINARY)):
        return _sa(type(t).__name__, _kw(length=t.length))
    if isinstance(t, sqltypes.BLOB):
        return _sa("BLOB")
    if isinstance(t, sqltypes.LargeBinary):
        return _sa("LargeBinary")

    if isinstance(t, sqltypes.UUID):
        return _sa("UUID")
    if isinstance(t, sqltypes.Uuid):
        return _sa("Uuid")

    return _as_reflected(t)


def _string(t: sqltypes.String) -> Emitted | None:
    from sqlalchemy.dialects import mysql
    from sqlalchemy.dialects.mysql import types as mysql_types

    collation = t.collation
    if isinstance(t, mysql_types._StringType):
        # A character set or `BINARY`/`NATIONAL` is MySQL's alone; the generic types cannot say it.
        mysql_only = _kw(
            charset=t.charset,
            national=t.national,
            binary=t.binary,
            ascii=t.ascii,
            unicode=t.unicode,
        )
        name = type(t).__name__
        if isinstance(t, (mysql.TINYTEXT, mysql.MEDIUMTEXT, mysql.LONGTEXT)) or mysql_only:
            length = (
                _kw(length=t.length) if name in ("CHAR", "VARCHAR", "NCHAR", "NVARCHAR") else ""
            )
            args = ", ".join(x for x in (length, mysql_only, _kw(collation=collation)) if x)
            return _of("mysql", name, args)
    if type(t).__module__.startswith("sqlalchemy.dialects.") and type(t).__name__ not in (
        "CHAR",
        "VARCHAR",
        "NCHAR",
        "NVARCHAR",
        "TEXT",
    ):
        return _as_reflected(t)  # CITEXT and the like: the dialect's own
    if isinstance(t, sqltypes.Text):
        return _sa("TEXT", _kw(collation=collation))
    name = type(t).__name__.upper()
    if name not in ("CHAR", "VARCHAR", "NCHAR", "NVARCHAR"):
        name = "VARCHAR"
    return _sa(name, _kw(length=t.length, collation=collation))


def _as_reflected(t: TypeEngine[Any]) -> Emitted | None:
    """A type the table above has no rule for, written as SQLAlchemy prints it - if that is
    valid Python that builds it again, and from the module it lives in."""
    module = type(t).__module__
    rendered = repr(t)
    if module.startswith("sqlalchemy.dialects."):
        rendered = f"{module.split('.')[2]}.{rendered}"
    try:
        built = evaluate(rendered)
    except Exception:
        return None
    if not isinstance(built, TypeEngine):
        return None
    return Emitted(rendered)


@cache
def _namespace() -> Mapping[str, Any]:
    import sqlalchemy
    from sqlalchemy.dialects import mysql, postgresql, sqlite

    names: dict[str, Any] = {k: v for k, v in vars(sqlalchemy).items() if not k.startswith("_")}
    names.update({"mysql": mysql, "postgresql": postgresql, "sqlite": sqlite})
    from sqlalchemy.types import NullType

    names["NullType"] = NullType
    return names


def evaluate(source: str) -> Any:
    """Build what a piece of generated source builds, with what a generated module imports."""
    from .. import schema

    names = dict(_namespace())
    names.update({h: getattr(schema, h) for h in HELPERS})
    return eval(source, {"__builtins__": {}}, names)


def sqlalchemy_names(source: str) -> set[str]:
    """The names a piece of source uses that come from ``sqlalchemy`` or a dialect module."""
    found = set()
    for node in ast.walk(ast.parse(source, mode="eval")):
        if isinstance(node, ast.Name) and node.id in _namespace():
            found.add(node.id)
    return found


# -- the SQL type, as text a pattern can match ------------------------------------------------


def type_names(
    reflected: TypeEngine[Any], raw: str | None, dialect: Dialect | None, extra: Iterable[str] = ()
) -> tuple[str, ...]:
    """Every spelling of a column's SQL type an ``include-types`` pattern may be written against.

    The database's own (``character varying(36)``, ``tinyint(1)``, ``int unsigned``), SQLAlchemy's
    (``VARCHAR(36)``, ``TINYINT(1)``, ``INTEGER UNSIGNED``), each with and without its arguments,
    and the short names people write (``TIMESTAMP`` for ``timestamp without time zone``,
    ``TIMESTAMPTZ`` for the zoned one). Upper case; matching ignores case anyway.
    """
    texts = [raw, *extra]
    with contextlib.suppress(Exception):  # NullType and friends compile to nothing
        texts.append(str(reflected.compile(dialect=dialect)))
    forms: list[str] = []

    def add(text: str) -> None:
        if text and text not in forms:
            forms.append(text)

    for text in filter(None, texts):
        upper = " ".join(text.upper().split())
        for form in (upper, re.sub(r"\s*,\s*", ",", upper), re.sub(r"\s*\([^)]*\)", "", upper)):
            add(form.strip())
    for form in list(forms):
        if form.endswith(" WITHOUT TIME ZONE"):
            add(form.removesuffix(" WITHOUT TIME ZONE"))
        if form.endswith(" WITH TIME ZONE"):
            add(
                form.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ").replace(
                    "TIME WITH TIME ZONE", "TIMETZ"
                )
            )
        if form.startswith("CHARACTER VARYING"):
            add("VARCHAR" + form.removeprefix("CHARACTER VARYING"))
        elif form.startswith("CHARACTER"):
            add("CHAR" + form.removeprefix("CHARACTER"))
    return tuple(forms)


# -- what a forced type turns a column into ---------------------------------------------------


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def resolve(forced: ForcedType) -> Emitted:
    """A forced type's target as source, checked by building it - so a typo fails here, naming
    the forced type, and not later as a generated module that does not import.

    For a ``converter`` this is the converter alone (``MoneyConverter()``); the column's own
    type is wrapped around it when the column is written."""
    if forced.name is not None:
        return _resolve_name(forced.name)
    if forced.converter is not None:
        return _resolve_converter(forced.converter)
    return _resolve_sqlalchemy_type(str(forced.sqlalchemy_type))


def _split_call(text: str, what: str) -> tuple[str, str | None]:
    """``Name(args)`` into ``Name`` and the normalised ``args``, which must be literals."""
    head, paren, rest = text.strip().partition("(")
    if not paren:
        return head.strip(), None
    try:
        call = ast.parse(f"f({rest}", mode="eval").body
    except SyntaxError:
        raise ValidationError(f"{what} {text!r} is not a name, or a name with arguments") from None
    assert isinstance(call, ast.Call)
    for node in [*call.args, *(k.value for k in call.keywords)]:
        try:
            ast.literal_eval(node)
        except ValueError:
            raise ValidationError(
                f"{what} {text!r}: arguments must be plain values - numbers, strings, "
                "True/False/None, lists"
            ) from None
    if any(k.arg is None for k in call.keywords):
        raise ValidationError(f"{what} {text!r}: no ** arguments")
    args = ", ".join(
        [ast.unparse(a) for a in call.args]
        + [f"{k.arg}={ast.unparse(k.value)}" for k in call.keywords]
    )
    return head.strip(), args


@cache
def _resolve_name(name: str) -> Emitted:
    what = "the forced type name"
    head, args = _split_call(name, what)
    if head in HELPERS:
        if args:
            raise ValidationError(f"{what} {name!r}: {head}() takes no arguments")
        return Emitted(f"{head}()", frozenset({("ekbatan.schema", head)}))
    dialect, dot, type_name = head.rpartition(".")
    if dot and dialect not in DIALECT_MODULES:
        raise ValidationError(
            f"{what} {name!r}: a dialect type is written mysql.X, postgresql.X or sqlite.X; "
            "your own class goes in converter, a library's type in sqlalchemy-type"
        )
    if not _IDENTIFIER.fullmatch(type_name):
        raise ValidationError(f"{what} {name!r} is not a type name")
    source = f"{head}({args or ''})"
    try:
        built = evaluate(source)
    except Exception as error:
        raise ValidationError(
            f"{what} {name!r} is not a SQLAlchemy type or one of {sorted(HELPERS)}: "
            f"{type(error).__name__}: {error}"
        ) from None
    if not isinstance(built, TypeEngine):
        raise ValidationError(f"{what} {name!r} builds {type(built).__name__}, not a column type")
    return Emitted(source)


def _import(path: str, what: str, kind: str) -> tuple[str, str, str | None, Any]:
    """A dotted path, imported: its module, its name, its arguments, and the object itself."""
    head, args = _split_call(path, what)
    if ":" in head:
        module_name, _, attribute = head.partition(":")
    else:
        module_name, _, attribute = head.rpartition(".")
    parts = module_name.split(".") if module_name else []
    if not parts or not all(_IDENTIFIER.fullmatch(p) for p in [*parts, attribute]):
        raise ValidationError(
            f"{what} {path!r} must be a dotted path to a {kind}: myapp.module.Name, or "
            "myapp.module:Name"
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        raise ValidationError(
            f"{what} {path!r}: cannot import {module_name}: {type(error).__name__}: {error}. "
            f"schemagen imports it to check the {kind}, so it must import without the generated "
            f"schema - keep your {kind}s in a module that does not import it"
        ) from None
    try:
        target = getattr(module, attribute)
    except AttributeError:
        raise ValidationError(f"{what} {path!r}: {module_name} has no {attribute}") from None
    return module_name, attribute, args, target


def _is_converter(value: object) -> bool:
    return callable(getattr(value, "from_db", None)) and callable(getattr(value, "to_db", None))


@cache
def _resolve_converter(path: str) -> Emitted:
    what = "the forced converter"
    module_name, attribute, args, target = _import(path, what, "converter")
    imports = frozenset({(module_name, attribute)})
    lacks = (
        f"{what} {path!r}: {attribute} has no from_db() and to_db(). A converter is any class "
        "with from_db(value) and to_db(value)"
    )
    if isinstance(target, TypeEngine) or (
        isinstance(target, type) and issubclass(target, TypeEngine)
    ):
        raise ValidationError(f"{lacks}; a SQLAlchemy type goes in sqlalchemy-type")
    if not isinstance(target, type) and _is_converter(target):
        if args is not None:
            raise ValidationError(
                f"{what} {path!r}: {attribute} is a converter already; drop the ()"
            )
        return Emitted(attribute, imports)
    if isinstance(target, type) and not _is_converter(target):
        raise ValidationError(lacks)
    if not callable(target):
        raise ValidationError(lacks)
    try:
        built = eval(f"t({args or ''})", {"__builtins__": {}}, {"t": target})
    except Exception as error:
        raise ValidationError(
            f"{what} {path!r} could not be built: {type(error).__name__}: {error}"
        ) from None
    if not _is_converter(built):
        raise ValidationError(
            f"{what} {path!r} builds {type(built).__name__}, which has no from_db() and to_db()"
        )
    return Emitted(f"{attribute}({args or ''})", imports)


@cache
def _resolve_sqlalchemy_type(path: str) -> Emitted:
    what = "the forced sqlalchemy-type"
    module_name, attribute, args, target = _import(path, what, "type")
    imports = frozenset({(module_name, attribute)})
    if isinstance(target, TypeEngine):
        if args is not None:
            raise ValidationError(f"{what} {path!r}: {attribute} is a type instance; drop the ()")
        return Emitted(attribute, imports)
    if not callable(target):
        raise ValidationError(f"{what} {path!r}: {attribute} is not a type")
    try:
        built = eval(f"t({args or ''})", {"__builtins__": {}}, {"t": target})
    except Exception as error:
        raise ValidationError(
            f"{what} {path!r} could not be built: {type(error).__name__}: {error}"
        ) from None
    if not isinstance(built, TypeEngine):
        hint = (
            "; it has from_db and to_db, so it goes in converter"
            if _is_converter(built)
            else " - a TypeDecorator subclass, a TypeEngine, or a function returning one"
        )
        raise ValidationError(
            f"{what} {path!r} builds {type(built).__name__}, not a SQLAlchemy type{hint}"
        )
    return Emitted(f"{attribute}({args or ''})", imports)
