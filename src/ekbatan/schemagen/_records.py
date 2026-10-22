"""The record types: one ``TypedDict`` per table, a key per column, typed as the column reads.

jOOQ generates a ``WalletsRecord`` beside the ``WALLETS`` table, and a repository's
``toRecord`` / ``fromRecord`` are typed by it. Here the same name is a ``TypedDict``: to
SQLAlchemy it is a plain ``dict``, so it costs nothing at run time, and to a type checker it says
which columns a table has and what each holds.

The type of a column is read from the very type object the generated module builds for it:

- a **converter** gives its user type - the return annotation of its ``from_db``, else the
  second argument of its ``Converter[DbT, UserT]`` base. Neither is an error, never ``Any``;
- a **TypeDecorator** gives what its ``process_result_value`` is annotated to return, or, when
  it converts nothing on the way out, what its ``impl`` reads as; any other type is
  SQLAlchemy's ``python_type`` for it;
- a timestamp is a time kind (:mod:`ekbatan.time`): zone-less is ``Instant``, or
  ``LocalDateTime`` where a forced type names it; zoned is ``OffsetDateTime``;
- JSON is :data:`ekbatan.schema.JsonValue`, an enum the ``Literal`` of its values, an array a
  ``list`` of its item, a MySQL ``SET`` a ``set[str]``;
- a type nothing can say anything about (``sql_type("xml")``, a library type with no
  ``python_type``) is ``Any``, and the run says so.

A nullable column is ``X | None``. A column the database fills itself - a server default, an
identity, a computed column, an auto-increment - is ``NotRequired``: an insert may leave it out,
and for a computed column must.
"""

from __future__ import annotations

import ast
import importlib
import keyword
import re
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypeAliasType, TypeVar

from sqlalchemy import types as sqltypes
from sqlalchemy.types import TypeEngine

from ..errors import ValidationError

__all__ = ["Field", "Unknowable", "converter_user_type", "python_type", "record_name", "render"]


class Unknowable(Exception):
    """Nothing says what a column's values are; the record says ``Any``, and a note says why."""


def record_name(base: str) -> str:
    """jOOQ's name for a table's record: ``wallets`` -> ``WalletsRecord``,
    ``event_notifications`` -> ``EventNotificationsRecord``."""
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", base) if p]
    name = "".join(p[:1].upper() + p[1:].lower() for p in parts)
    if not name or name[0].isdigit():
        name = f"T{name}"
    return f"{name}Record"


# -- a column's Python type -----------------------------------------------------------------


def _decorated(t: sqltypes.TypeDecorator[Any]) -> object:
    """What a ``TypeDecorator`` reads back as: its ``process_result_value``'s return annotation
    when it converts, else what its ``impl`` reads as.

    SQLAlchemy drops a type's generic argument at run time (``TypeDecorator[datetime]`` is
    ``TypeDecorator`` once the class exists), so the annotation is the only thing that says.
    """
    own = type(t).process_result_value
    if own is sqltypes.TypeDecorator.process_result_value:
        return python_type(t.impl_instance)
    try:
        found = typing.get_type_hints(own).get("return")
    except Exception:
        found = None
    if found is not None:
        kept = [a for a in _members(found) if a is not types.NoneType and a is not None]
        if kept and all(_concrete(a) for a in kept):
            return kept[0] if len(kept) == 1 else typing.Union.__getitem__(tuple(kept))
    raise Unknowable(
        f"{type(t).__name__} converts what it reads, and its process_result_value() has no "
        "return annotation saying to what"
    )


def _dialect_type(t: TypeEngine[Any]) -> object | None:
    """The dialect types SQLAlchemy gives no ``python_type``: what their drivers read them as,
    measured on psycopg, asyncpg, PyMySQL and aiomysql (``test_schemagen_types``)."""
    from datetime import date, datetime
    from decimal import Decimal
    from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

    from sqlalchemy.dialects import mysql, postgresql
    from sqlalchemy.dialects.postgresql.ranges import MultiRange, Range

    if isinstance(t, postgresql.INET):
        return IPv4Address | IPv6Address
    if isinstance(t, postgresql.CIDR):
        return IPv4Network | IPv6Network
    if isinstance(t, postgresql.BIT):
        raise Unknowable("BIT reads as str on psycopg and as asyncpg's BitString on asyncpg")
    texts = (
        postgresql.MACADDR,
        postgresql.MACADDR8,
        postgresql.MONEY,
        postgresql.TSVECTOR,
        postgresql.TSQUERY,
        postgresql.JSONPATH,
        postgresql.REGCLASS,
        postgresql.REGCONFIG,
        mysql.INET4,
        mysql.INET6,
    )
    if isinstance(t, texts):
        return str
    if isinstance(t, (postgresql.OID, mysql.YEAR, mysql.BIT)):
        return int
    if isinstance(t, postgresql.HSTORE):
        return dict[str, str | None]
    if isinstance(t, postgresql.DOMAIN):
        return python_type(t.data_type)
    bounds: list[tuple[tuple[type, ...], type]] = [
        ((postgresql.INT4RANGE, postgresql.INT8RANGE), int),
        ((postgresql.INT4MULTIRANGE, postgresql.INT8MULTIRANGE), int),
        ((postgresql.NUMRANGE, postgresql.NUMMULTIRANGE), Decimal),
        ((postgresql.DATERANGE, postgresql.DATEMULTIRANGE), date),
        ((postgresql.TSRANGE, postgresql.TSTZRANGE), datetime),
        ((postgresql.TSMULTIRANGE, postgresql.TSTZMULTIRANGE), datetime),
    ]
    for classes, bound in bounds:
        if isinstance(t, classes):
            multi = isinstance(t, postgresql.ranges.AbstractMultiRange)
            return MultiRange[bound] if multi else Range[bound]  # type: ignore[valid-type]
    return None


def _members(hint: object) -> tuple[object, ...]:
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        return typing.get_args(hint)
    return (hint,)


def _concrete(hint: object) -> bool:
    """Whether a hint names real types all the way down: no type variable, no ``Any``, no
    unresolved forward reference."""
    if hint is Any or isinstance(hint, (TypeVar, typing.ForwardRef, str)):
        return False
    if isinstance(hint, (typing.ParamSpec, typing.TypeVarTuple)):
        return False
    return all(_concrete(a) for a in typing.get_args(hint) if a is not Ellipsis and a != ())


def python_type(column_type: TypeEngine[Any]) -> object:
    """What a column of this type reads back as, as a type hint.

    Raises :class:`Unknowable` when nothing says. A converter's column is not handled here - see
    :func:`converter_user_type` - because its error has to name the forced type.
    """
    from ..schema import JsonValue, _SqlType

    t = column_type
    if isinstance(t, sqltypes.NullType):
        raise Unknowable("a type SQLAlchemy cannot read")
    if isinstance(t, _SqlType):
        raise Unknowable(f"sql_type({t.spelling!r}): values are what the driver gives")
    if isinstance(t, sqltypes.JSON):
        return JsonValue
    if isinstance(t, sqltypes.Enum):
        if t.enum_class is not None:
            return t.enum_class
        return Literal[tuple(t.enums)] if t.enums else str
    from sqlalchemy.dialects import mysql

    if isinstance(t, mysql.SET):
        return set[str]
    if isinstance(t, sqltypes.ARRAY):
        item = python_type(t.item_type)
        hint: object = item
        for _ in range(max(t.dimensions or 1, 1)):
            hint = list[hint]  # type: ignore[valid-type]
        return hint
    from ..schema import Converted

    if isinstance(t, Converted):
        return converter_user_type(t._converter, repr(t._converter))
    if isinstance(t, sqltypes.TypeDecorator):
        return _decorated(t)
    if isinstance(t, sqltypes.DateTime):
        # The kind of time, not the class: a zone-less column holds a moment as UTC digits (an
        # Instant, unless a forced type says LocalDateTime), a zoned one a moment with an offset.
        from ..time import Instant, OffsetDateTime

        return OffsetDateTime if t.timezone else Instant
    known = _dialect_type(t)
    if known is not None:
        return known
    try:
        found = t.python_type
    except NotImplementedError:
        raise Unknowable(f"{type(t).__name__} does not say what it holds") from None
    return found


def converter_user_type(converter: object, path: str) -> object:
    """The type a converter's ``from_db`` gives: its return annotation, else its
    ``Converter[DbT, UserT]`` base. Neither is an error naming the converter."""
    from ..schema import Converter

    method = getattr(converter, "from_db", None)
    problem = "from_db() has no return annotation"
    if method is not None:
        try:
            hints = typing.get_type_hints(method)
        except Exception as error:  # an annotation naming something the module cannot see
            hints = {}
            problem = (
                f"from_db()'s annotations cannot be read: {type(error).__name__}: {error} - "
                "is the type imported only under TYPE_CHECKING?"
            )
        found = hints.get("return")
        if found is not None and (found is Any or _concrete(found)):
            return found
        if found is not None:
            problem = f"from_db() is annotated to return {found!r}, which names no real type"
    for klass in type(converter).__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            if typing.get_origin(base) is Converter:
                args = typing.get_args(base)
                if len(args) == 2 and _concrete(args[1]):
                    return args[1]
    raise ValidationError(
        f"the forced converter {path!r}: schemagen cannot tell what type it gives, so it cannot "
        f"type the column in the record - {problem}, and the class does not extend "
        f"Converter[DbT, UserT]. Annotate it: `def from_db(self, value: str) -> Currency:`"
    )


# -- writing a hint ----------------------------------------------------------------------------


#: Asked for a name to write a class under: (module, attribute) -> the name in the module.
Namer = Callable[[str, str], str]


def hint_source(hint: object, name_for: Namer) -> str:
    """A type hint as source, asking ``name_for`` for every name it needs imported."""
    if hint is None or hint is types.NoneType:
        return "None"
    if hint is Any:
        return name_for("typing", "Any")
    if hint is Ellipsis:
        return "..."
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Union or origin is types.UnionType:
        parts: list[str] = []
        for arg in args:
            text = hint_source(arg, name_for)
            if text not in parts:
                parts.append(text)
        if "None" in parts:  # None last, as people write it
            parts = [p for p in parts if p != "None"] + ["None"]
        return " | ".join(parts)
    if origin is Literal:
        return f"{name_for('typing', 'Literal')}[{', '.join(_literal(a, name_for) for a in args)}]"
    if origin is typing.Annotated:
        return hint_source(args[0], name_for)
    if origin is not None:
        inner = ", ".join(hint_source(a, name_for) for a in args) if args else "()"
        return f"{_named(origin, name_for)}[{inner}]"
    if isinstance(hint, (TypeVar, typing.ParamSpec, typing.TypeVarTuple)):
        raise ValidationError(f"{hint!r} is a type variable, which a record cannot name")
    if isinstance(hint, (str, typing.ForwardRef)):
        raise ValidationError(f"{hint!r} is a forward reference that could not be resolved")
    return _named(hint, name_for)


def _literal(value: object, name_for: Namer) -> str:
    if isinstance(value, Enum):
        return f"{_named(type(value), name_for)}.{value.name}"
    from ._types import lit

    return lit(value)


def _named(target: object, name_for: Namer) -> str:
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if isinstance(target, TypeAliasType):
        qualname = target.__name__
    if not isinstance(module, str) or not isinstance(qualname, str):
        raise ValidationError(f"{target!r} has no module and name to import it by")
    if module == "builtins":
        return qualname
    if "<locals>" in qualname or module == "__main__":
        raise ValidationError(
            f"{module}.{qualname} cannot be imported by name: define it at the top level of a "
            "module the generated schema can import"
        )
    head, dot, rest = qualname.partition(".")
    try:
        found = getattr(importlib.import_module(module), head)
    except Exception:
        raise ValidationError(f"{module}.{qualname} cannot be imported by name") from None
    if dot:
        for part in rest.split("."):
            found = getattr(found, part, None)
    if found is not target:
        raise ValidationError(f"importing {module}.{qualname} does not give {target!r}")
    return name_for(module, head) + (dot + rest if dot else "")


# -- writing a record --------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One key of a record: the column's name, its type, whether it may be NULL, and whether an
    insert may leave it out."""

    name: str
    hint: object
    nullable: bool
    optional: bool


def render(name: str, table: str, fields: list[Field], name_for: Namer) -> str:
    """The ``TypedDict`` for one table.

    Written as a class where it can be. A column whose name is not an identifier, or is the
    name of a type the record uses (a ``date`` column of type ``date``, which in a class body
    would name the field rather than the type), makes it the call form instead.
    """
    typed_dict = name_for("typing", "TypedDict")
    lines: list[tuple[str, str]] = []
    for f in fields:
        hint = hint_source(f.hint, name_for)
        if f.nullable and hint not in ("None", "Any") and not hint.endswith("| None"):
            hint = f"{hint} | None"
        if f.optional:
            hint = f"{name_for('typing', 'NotRequired')}[{hint}]"
        lines.append((f.name, hint))
    used = {
        node.id
        for _, hint in lines
        for node in ast.walk(ast.parse(hint, mode="eval"))
        if isinstance(node, ast.Name)
    }

    from ._types import lit

    doc = f"A row of `{table}`: what a repository's `to_record` writes and `from_record` reads."
    if all(n.isidentifier() and not keyword.iskeyword(n) and n not in used for n, _ in lines):
        body = "\n".join(f"    {n}: {h}" for n, h in lines) if lines else "    pass"
        return f'class {name}({typed_dict}):\n    """{doc}"""\n\n{body}'
    entries = "".join(f"\n        {lit(n)}: {h}," for n, h in lines)
    return f"#: {doc}\n{name} = {typed_dict}(\n    {lit(name)},\n    {{{entries}\n    }},\n)"
