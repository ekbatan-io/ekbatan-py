"""The connection the framework hands out, and the fluent read on it.

A repository that outgrows the built-in finders drops to SQL, and then has to turn the rows
into objects itself. Against a bare SQLAlchemy connection that is two steps and a
comprehension::

    rows = conn.execute(statement).all()
    return [self.from_record(row._mapping) for row in rows]

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

from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, Self, cast

from sqlalchemy import Connection

if TYPE_CHECKING:
    from sqlalchemy import Executable
    from sqlalchemy.engine import Result

__all__ = ["Db", "FetchResult"]


class FetchResult:
    """The rows of a statement, not yet turned into anything.

    :meth:`Db.rows` gives you one. Each terminal takes the mapper, so the mapping is the last
    thing you read - the same order as the Java port's ``fetch(this::fromRecord)``.

    Not generic, deliberately: the type comes from the mapper handed to the terminal, so one of
    these can be read as wallets on one line and as currency codes on the next.

    **The mapper is handed each row as a record** - the row's mapping, read by column name,
    ``row["currency"]`` - which is what a repository's ``from_record`` takes. Its parameter
    says which record it expects (``WalletsRecord``); the statement is not checked against it,
    as nothing can check a SQL statement's columns against a type before it runs. A lambda's
    row is a ``Mapping`` to a type checker, so it reads ``row["currency"]``; ``row.currency``
    is a type error.

    The names do **not** follow jOOQ's, and that is the one place this deliberately parts from
    it. There, ``fetchOne`` means *at most one, raise if there are two* and ``fetchAny`` means
    *the first*. A ``fetch_one`` that did not mean "fetch one" is a trap worth more than the
    familiarity of the spelling, so these say what they do.
    """

    __slots__ = ("_result",)

    def __init__(self, result: Result[Any]) -> None:
        self._result = result

    # ------------------------------------------------------------------ terminals

    def fetch[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> list[T]:
        """Every row, mapped. A ``list``, not a ``Sequence``: it is yours to mutate."""
        return [mapper(cast(R, row._mapping)) for row in self._result]

    def fetch_first[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> T | None:
        """The first row, or ``None``. The read that treats absence as ordinary."""
        row = self._result.first()
        return mapper(cast(R, row._mapping)) if row is not None else None

    def fetch_one[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> T:
        """Exactly one row, or raise.

        Raises if there are none *and* if there are two. For a query you believe returns one
        row - which is exactly the belief worth having checked.
        """
        return mapper(cast(R, self._result.one()._mapping))

    def fetch_one_or_none[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> T | None:
        """At most one row, or ``None``.

        The difference from :meth:`fetch_first` is what a second row means: here it is an
        error, there it is ignored. Reach for this when two rows would mean a broken invariant
        rather than a result to choose from.
        """
        row = self._result.one_or_none()
        return mapper(cast(R, row._mapping)) if row is not None else None

    def each[T, R: Mapping[Any, Any]](self, mapper: Callable[[R], T]) -> Iterator[T]:
        """Every row, lazily - so a caller that stops early has not mapped the rest.

        What :meth:`fetch` would be without the list, and what a scatter-gather comprehension
        across shards wants.
        """
        return (mapper(cast(R, row._mapping)) for row in self._result)

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
            yield [mapper(cast(R, row._mapping)) for row in chunk]

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
