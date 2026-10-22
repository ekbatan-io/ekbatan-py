"""The connection the framework hands out, and the fluent read on it.

A repository that outgrows the built-in finders drops to SQL, and then has to turn the rows
into objects itself. Against a bare SQLAlchemy connection that is two steps and a
comprehension::

    rows = conn.execute(statement).all()
    return [self.from_record(WalletsRecord(**row._mapping)) for row in rows]

The Java port says it in one clause, ``fetch(this::fromRecord)``, because jOOQ ends a query
with its mapping. SQLAlchemy has no such verb, so :class:`Db` supplies it::

    return conn.rows(statement).fetch(self.from_record)

Nothing is taken away to make room for it. :class:`Db` **is** a
:class:`sqlalchemy.Connection`, so ``execute``, ``begin``, ``exec_driver_sql`` and every other
method are the ones SQLAlchemy documents, unchanged and untouched. A query that has nothing to
map - a count, a rowcount, an ``EXISTS`` - still calls ``execute``, because ``rows`` would be
claiming a shape the answer does not have.

:class:`FetchResult` **holds** a :class:`~sqlalchemy.engine.Result` rather than subclassing
one, and calls only documented methods on it. Subclassing was the shorter route and was
rejected: reproducing a ``Result`` means setting ``_real_result``, ``_metadata``,
``_post_creational_filter`` and ``_unique_filter_state`` by hand, four private attributes whose
contract can change in a patch release, to inherit behaviour this needs none of.
"""

from __future__ import annotations

import contextlib
import inspect
import typing
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Self, cast

from sqlalchemy import Connection

if TYPE_CHECKING:
    from sqlalchemy import Executable
    from sqlalchemy.engine import Result

__all__ = ["Db", "FetchResult"]


def record_class(hint: object) -> type[dict[str, Any]] | None:
    """The record class a type names - a subclass of ``dict``, as schemagen writes
    ``WalletsRecord`` - or ``None``: ``dict`` itself, a ``TypedDict``, a ``Mapping``, a type
    variable, anything else."""
    if (
        isinstance(hint, type)
        and not typing.is_typeddict(hint)
        and issubclass(hint, dict)
        and hint is not dict
    ):
        return cast(type[dict[str, Any]], hint)
    return None


def records(kind: type[Any], keys: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[Any]:
    """One ``kind`` per row, each column under its name. Built without the record's keyword
    ``__init__`` - an empty one of the class, filled - which costs what ``dict(zip(...))``
    costs, so reading ten thousand rows into records is no slower than reading their
    mappings."""
    new: Callable[[type[Any]], Any] = dict.__new__
    fill: Callable[..., None] = dict.update
    out: list[Any] = []
    add = out.append
    for row in rows:
        record = new(kind)
        fill(record, zip(keys, row, strict=True))
        add(record)
    return out


def record_of(kind: type[Any], keys: Sequence[str], row: Sequence[Any]) -> Any:
    """One row as one ``kind`` - see :func:`records`."""
    record: dict[str, Any] = dict.__new__(kind)
    dict.update(record, zip(keys, row, strict=True))
    return record


#: The record class each mapper's first parameter names, by the function - a bound method's
#: function, so ``self.from_record`` is looked up once, not once per repository or per call.
_MAPPER_RECORDS: weakref.WeakKeyDictionary[Any, type[dict[str, Any]] | None] = (
    weakref.WeakKeyDictionary()
)


def _mapper_record(mapper: Callable[..., Any]) -> type[dict[str, Any]] | None:
    """The record class a mapper takes: what its first parameter is annotated as, when that is
    a record class; else, for a repository's own method (``self.from_record``,
    ``self.model_by_field_name``), the repository's record class; else ``None`` - the mapper
    takes the row's mapping."""
    # A decorated mapper - `functools.wraps` - is read as the function it wraps.
    given = mapper
    mapper = inspect.unwrap(mapper)
    function = getattr(mapper, "__func__", mapper)
    owner = getattr(given, "__self__", None) or getattr(mapper, "__self__", None)
    try:
        kind = _MAPPER_RECORDS[function]
    except (KeyError, TypeError):
        kind = None
        if getattr(function, "__annotations__", None):
            try:
                hints = typing.get_type_hints(function)
                code = function.__code__
                bound = 1 if owner is not None else 0
                names = code.co_varnames[bound : code.co_argcount]
                kind = record_class(hints.get(names[0])) if names else None
            except Exception:  # an annotation naming what the module cannot see; a builtin
                kind = None
        with contextlib.suppress(TypeError):  # not weakly referenceable: asked next time again
            _MAPPER_RECORDS[function] = kind
    if kind is None:
        kind = record_class(getattr(owner, "_record_type", None))
    return kind


class FetchResult:
    """The rows of a statement, not yet turned into anything.

    :meth:`Db.rows` gives you one. Each terminal takes the mapper, so the mapping is the last
    thing you read - the same order as the Java port's ``fetch(this::fromRecord)``.

    Not generic, deliberately: the type comes from the mapper handed to the terminal, so one of
    these can be read as wallets on one line and as currency codes on the next.

    **The mapper is handed each row read by column name.** A mapper whose first parameter is
    a record class - a repository's ``from_record(self, record: WalletsRecord)``, a function of
    your own typed the same way - is handed that class, built from the row, so
    ``record.column_currency()`` works (preferred), as does ``record["currency"]``; a
    repository's own method whose annotation cannot be read is handed the repository's record.
    Any other mapper - a lambda - is handed the row's mapping, ``row["currency"]``. The
    statement is not checked against the record, as nothing can check a SQL statement's columns
    against a type before it runs. A lambda's row is a ``Mapping`` to a type checker, so
    ``row.currency`` is a type error.

    The names do **not** follow jOOQ's, and that is the one place this deliberately parts from
    it. There, ``fetchOne`` means *at most one, raise if there are two* and ``fetchAny`` means
    *the first*. A ``fetch_one`` that did not mean "fetch one" is a trap worth more than the
    familiarity of the spelling, so these say what they do.
    """

    __slots__ = ("_result",)

    def __init__(self, result: Result[Any]) -> None:
        self._result = result

    # ------------------------------------------------------------------ terminals

    def _mapped[T](self, mapper: Callable[[Any], T], rows: Iterable[Any]) -> list[T]:
        kind = _mapper_record(mapper)
        if kind is None:
            return [mapper(row._mapping) for row in rows]
        return [mapper(r) for r in records(kind, tuple(self._result.keys()), rows)]

    def _one_mapped[T](self, mapper: Callable[[Any], T], row: Any) -> T:
        kind = _mapper_record(mapper)
        if kind is None:
            return mapper(row._mapping)
        return mapper(record_of(kind, tuple(self._result.keys()), row))

    def fetch[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> list[T]:
        """Every row, mapped. A ``list``, not a ``Sequence``: it is yours to mutate."""
        return self._mapped(mapper, self._result)

    def fetch_first[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> T | None:
        """The first row, or ``None``. The read that treats absence as ordinary."""
        row = self._result.first()
        return self._one_mapped(mapper, row) if row is not None else None

    def fetch_one[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> T:
        """Exactly one row, or raise.

        Raises if there are none *and* if there are two. For a query you believe returns one
        row - which is exactly the belief worth having checked.
        """
        return self._one_mapped(mapper, self._result.one())

    def fetch_one_or_none[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> T | None:
        """At most one row, or ``None``.

        The difference from :meth:`fetch_first` is what a second row means: here it is an
        error, there it is ignored. Reach for this when two rows would mean a broken invariant
        rather than a result to choose from.
        """
        row = self._result.one_or_none()
        return self._one_mapped(mapper, row) if row is not None else None

    def each[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> Iterator[T]:
        """Every row, lazily - so a caller that stops early has not mapped the rest.

        What :meth:`fetch` would be without the list, and what a scatter-gather comprehension
        across shards wants.
        """
        kind = _mapper_record(mapper)
        if kind is None:
            return (mapper(cast(R, row._mapping)) for row in self._result)
        keys = tuple(self._result.keys())
        return (mapper(record_of(kind, keys, row)) for row in self._result)

    # ------------------------------------------------------------------ shaping

    def yield_per(self, num: int) -> Self:
        """Read ``num`` rows at a time rather than all of them.

        Returns this result, so it chains ahead of the terminal.
        """
        self._result = self._result.yield_per(num)
        return self

    def partitions[T, R: Mapping[Any, Any]](
        self, mapper: Callable[[R], T], size: int | None = None
    ) -> Iterator[list[T]]:
        """Batches of mapped rows, lazily.

        ``size`` defaults to whatever :meth:`yield_per` was given, which is the usual way to
        reach this - so the common call passes only the mapper.
        """
        for chunk in self._result.partitions(size):
            yield self._mapped(mapper, chunk)

    def unique(self, strategy: Callable[[Any], Any] | None = None) -> Self:
        """Drop duplicate rows, before mapping.

        ``strategy`` decides what "duplicate" means; the default is the row itself, which
        needs every value in it to be hashable.
        """
        self._result = self._result.unique(strategy)
        return self


class Db(Connection):
    """A SQLAlchemy connection, with one method added.

    Every connection this framework hands out is one of these - what ``db()``,
    ``readonly_db()``, ``tx_db_else_db()`` and their scatter forms yield, and what a
    :class:`~ekbatan.Transaction` runs its statements on. Nothing about a connection changes:
    this is a subclass, so everything SQLAlchemy documents is still here and still means what
    its documentation says.

    It is constructed directly rather than through ``engine.connect()``, which is what that
    method does anyway - ``return self._connection_cls(self)``. Going through the constructor
    keeps this off a private attribute of ``Engine``.
    """

    __slots__ = ()

    def __enter__(self) -> Self:
        """The base class promises a ``Connection``; this promises the subclass.

        Without it ``with self.db() as conn`` hands a type checker a plain ``Connection`` and
        ``conn.rows(...)`` is an error in an editor and fine at runtime.
        """
        return self

    def execution_options(self, **opt: Any) -> Self:
        """As SQLAlchemy's, and it already returns ``self`` - this only says so in the type.

        Every accessor in the framework calls it to ask for ``AUTOCOMMIT``, and each one would
        otherwise hand back something typed as a plain connection.
        """
        super().execution_options(**opt)
        return self

    def rows(self, statement: Executable) -> FetchResult:
        """Run the statement and hand back its rows, for mapping.

        For a statement whose answer is rows to turn into objects. Everything else - a count,
        a rowcount, an ``EXISTS``, DDL - is ``execute``, unchanged.
        """
        return FetchResult(self.execute(statement))
