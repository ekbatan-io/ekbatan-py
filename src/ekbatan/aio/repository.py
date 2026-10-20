"""Reading and writing rows, awaited.

The counterpart to :mod:`ekbatan.repository`. A repository here is the sync one's core - the
same construction checks, mapping, shard routing, soft-delete filter, statements and
optimistic-lock rules, inherited rather than copied - with the methods that run a statement
written as coroutines::

    class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
        def to_record(self, wallet: Wallet) -> WalletsRecord: ...    # plain def, as in sync
        def from_record(self, record: WalletsRecord) -> Wallet: ...

        async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
            return await self.find_where(WALLETS.c.owner_id == owner_id)

It opens no transaction, as the sync one opens none: inside a unit of work it joins the one the
executor opened, outside one each statement commits itself.
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import ColumnElement, Row, insert

from ..domain import Entity, Model, Persistable
from ..repository import (
    _ONE_STATEMENT,
    _must_be_entity,
    _must_be_model,
    _RepositoryCore,
    _to_column,
)
from ..sharding import ShardIdentifier, ShardingStrategy
from .persistence import AsyncDatabaseRegistry, AsyncDb

__all__ = [
    "AbstractAsyncRepository",
    "AsyncEntityRepository",
    "AsyncModelRepository",
    "AsyncRepository",
]


class AsyncRepository[M: Persistable[Any, Any]](Protocol):
    """What the async executor asks of a repository - :class:`~ekbatan.repository.Repository`,
    with the writes awaited."""

    model_type: type[M]
    sharding: ShardingStrategy

    @abstractmethod
    def shard_of(self, model: M) -> ShardIdentifier: ...

    @abstractmethod
    async def add(self, model: M) -> M: ...

    @abstractmethod
    async def add_no_result(self, model: M) -> None: ...

    @abstractmethod
    async def add_all(self, models: Sequence[M]) -> list[M]: ...

    @abstractmethod
    async def add_all_no_result(self, models: Sequence[M]) -> None: ...

    @abstractmethod
    async def update(self, model: M) -> M: ...

    @abstractmethod
    async def update_no_result(self, model: M) -> None: ...

    @abstractmethod
    async def update_all(self, models: Sequence[M]) -> list[M]: ...

    @abstractmethod
    async def update_all_no_result(self, models: Sequence[M]) -> None: ...

    @abstractmethod
    async def all(self, *, shard: ShardIdentifier | None = None) -> list[M]: ...


class AbstractAsyncRepository[M: Persistable[Any, Any], R: Mapping[str, Any], K](
    _RepositoryCore[M, R, K], AsyncRepository[M]
):
    """Rows of one table, as models, awaited - the async
    :class:`~ekbatan.repository.AbstractRepository`.

    Every method here is the sync one's, line for line, with ``await`` where it waits on the
    database; what it sends and what the answer means come from the shared core.
    """

    _EXTEND = "AsyncModelRepository or AsyncEntityRepository"

    registry: AsyncDatabaseRegistry

    # ------------------------------------------------------- where the queries run

    @asynccontextmanager
    async def db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> AsyncIterator[AsyncDb]:
        """A writer connection for one shard, outside any transaction. Each statement commits.

        See the sync :meth:`~ekbatan.repository.AbstractRepository.db`: it does **not** join an
        open transaction, which is why :meth:`tx_db_else_db` is the usual choice.
        """
        async with AsyncDb(self._manager(key, model, shard).primary) as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            yield conn

    async def dbs(self) -> AsyncIterator[tuple[ShardIdentifier, AsyncDb]]:
        """Every shard's writer, one at a time, outside any transaction - the sync
        :meth:`~ekbatan.repository.AbstractRepository.dbs`, as an async generator.

        One at a time and lazy, as in sync: the caller decides what to do on each shard, and one
        that stops at the first shard that answers has not opened the rest. An unsharded
        repository visits only the default shard.
        """
        for shard in self._scatter_shards():
            async with self.db(shard=shard) as conn:
                yield shard, conn

    @asynccontextmanager
    async def readonly_db(
        self, *, key: K | None = None, shard: ShardIdentifier | None = None
    ) -> AsyncIterator[AsyncDb]:
        """The **replica**, for a read that can live with replication lag - the primary when
        the shard has none. Never joins an open transaction, as the sync one does not."""
        async with AsyncDb(self._manager(key, None, shard).replica) as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            yield conn

    async def readonly_dbs(self) -> AsyncIterator[tuple[ShardIdentifier, AsyncDb]]:
        """Every shard's **replica**, one at a time - as :meth:`dbs`."""
        for shard in self._scatter_shards():
            async with self.readonly_db(shard=shard) as conn:
                yield shard, conn

    def tx_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> AsyncDb | None:
        """The transaction already open on this shard in this task, or ``None``."""
        current: AsyncDb | None = self._manager(key, model, shard).current()
        return current

    @asynccontextmanager
    async def tx_db_else_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> AsyncIterator[AsyncDb]:
        """The transaction open on this shard, or no transaction at all if there is none. What
        every built-in write uses. It never opens one."""
        async with self._manager(key, model, shard).connection() as conn:
            yield conn

    @asynccontextmanager
    async def _reader(
        self, *, key: K | None = None, shard: ShardIdentifier | None = None
    ) -> AsyncIterator[AsyncDb]:
        """What every built-in read uses: :meth:`db` - the primary, outside any transaction,
        exactly as the sync repository and Java's do."""
        async with self.db(key=key, shard=shard) as conn:
            yield conn

    async def _on_each[T](
        self,
        shards: Sequence[ShardIdentifier],
        read: Callable[[ShardIdentifier], Awaitable[T]],
    ) -> list[T]:
        """``read`` on every shard **at the same time**, the answers in shard order.

        The one place the async repository does better than the sync one, which visits shards
        one after another: a read across sixteen shards costs about one shard's round trip.
        All or nothing, as the sync read is: if one shard fails, the reads still running on the
        others are cancelled and that shard's error is raised - the same exception the sync
        read would raise, with a note naming the shard. There are no partial results.
        """
        if len(shards) == 1:
            return [await read(shards[0])]
        answers: dict[ShardIdentifier, T] = {}
        failures: dict[ShardIdentifier, Exception] = {}

        async def one(shard: ShardIdentifier) -> None:
            try:
                answers[shard] = await read(shard)
            except Exception as error:
                failures[shard] = error
                raise

        try:
            async with asyncio.TaskGroup() as group:
                for shard in shards:
                    group.create_task(one(shard))
        except BaseExceptionGroup:
            if not failures:
                raise
            # Raised outside the handler, so the error is the shard's own and not chained to
            # the group that carried it.
            failed = self._shard_failure(shards, failures, cancelled=True)
        else:
            return [answers[shard] for shard in shards]
        raise failed

    async def _first_answer[T](
        self,
        shards: Sequence[ShardIdentifier],
        read: Callable[[ShardIdentifier], Coroutine[Any, Any, T | None]],
    ) -> T | None:
        """``read`` on every shard at the same time, answered as the sync call answers.

        The sync call asks shard by shard and stops at the first that answers. Here every shard
        is asked at once, but the answers are taken in shard order: the first shard with an
        answer wins and the reads still running are cancelled, and a shard's failure fails the
        read only when every shard before it answered ``None`` - exactly when the sync call
        would have reached it. A broken shard after the one that answers costs nothing, in
        either mode. ``None`` is "not here".
        """
        if len(shards) == 1:
            return await read(shards[0])
        tasks: dict[ShardIdentifier, asyncio.Task[T | None]] = {
            each: asyncio.create_task(read(each)) for each in shards
        }
        try:
            for each in shards:
                try:
                    found = await tasks[each]
                except Exception as error:
                    failed, failure = each, error
                    break
                if found is not None:
                    return found
            else:
                return None
        finally:
            for task in tasks.values():
                task.cancel()
            # Every task is waited for, so each connection is back in its pool and no error
            # is left unretrieved.
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        # Raised outside the handler, so the error is the shard's own and not chained.
        raise self._shard_failure(shards, {failed: failure}, cancelled=True)

    # ------------------------------------------------------------------ reads

    async def find(self, key: K) -> M | None:
        """The row, or ``None``."""
        async with self._reader(key=key) as conn:
            row = (await conn.execute(self._by_id(key))).first()
        return self._read_back(row) if row is not None else None

    async def get(self, key: K) -> M:
        """The row, or raise."""
        return self._found(await self.find(key), key)

    async def find_many(self, keys: Iterable[K]) -> list[M]:
        """The rows that exist, in no promised order - one query per shard the keys live on,
        the shards at the same time. Duplicates are collapsed; no keys is no query."""
        by_shard = self._keys_by_shard(keys)

        async def read(shard: ShardIdentifier) -> list[M]:
            async with self._reader(shard=shard) as conn:
                found = await conn.execute(self._among(conn, by_shard[shard]))
                return [self._read_back(r) for r in found]

        if not by_shard:
            return []
        return [model for found in await self._on_each(list(by_shard), read) for model in found]

    async def find_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> list[M]:
        """Live rows matching, on the shard named or on every shard."""
        return [self._read_back(r) for r in await self._read(self._where(*criteria), shard)]

    async def find_one_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> M | None:
        """The one row matching, or ``None``; a shard holding two is refused.

        Across shards every shard is asked at once, and the answer is the sync one's: the first
        shard in order that has the row; the reads after it are cancelled, and a shard after it
        that fails does not fail the read.
        """
        stmt = self._one_where(*criteria)

        async def read(each: ShardIdentifier) -> M | None:
            async with self._reader(shard=each) as conn:
                return self._unique(list(await conn.execute(stmt)))

        return await self._first_answer(self._shards(shard), read)

    async def all(self, *, shard: ShardIdentifier | None = None) -> list[M]:
        return await self.find_where(shard=shard)

    async def exists(self, key: K) -> bool:
        stmt = self._presence(self.id_column == _to_column(key))
        async with self._reader(key=key) as conn:
            return (await conn.execute(stmt)).first() is not None

    async def exists_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> bool:
        """Whether any row matches - one row read per shard at most, the shards at once, the
        answer the sync one's: the first shard in order with a row settles it."""
        stmt = self._presence(*criteria)

        async def read(each: ShardIdentifier) -> bool | None:
            async with self._reader(shard=each) as conn:
                return True if (await conn.execute(stmt)).first() is not None else None

        return await self._first_answer(self._shards(shard), read) is not None

    async def count(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> int:
        return sum(int(r[0]) for r in await self._read(self._count(*criteria), shard))

    async def _read(self, stmt: Any, shard: ShardIdentifier | None) -> list[Row[Any]]:
        """Run a read on the shard named, or on every shard at once when none is."""

        async def read(each: ShardIdentifier) -> list[Row[Any]]:
            async with self._reader(shard=each) as conn:
                return list(await conn.execute(stmt))

        return [row for rows in await self._on_each(self._shards(shard), read) for row in rows]

    # ----------------------------------------------------------------- writes

    async def add(self, model: M) -> M:
        """Insert one row and hand back what the database now holds."""
        return (await self.add_all([self._one(model)]))[0]

    async def add_no_result(self, model: M) -> None:
        await self.add_all_no_result([self._one(model)])

    async def add_all(self, models: Sequence[M]) -> list[M]:
        """Insert, one statement however many rows, and read the rows back - see the sync
        :meth:`~ekbatan.repository.AbstractRepository.add_all`."""
        if not self._all(models):
            return []
        shard, rows = self._insert_rows(models)

        async with self.tx_db_else_db(shard=shard) as conn:
            if conn.dialect.insert_returning:
                written = await conn.execute(
                    self._insert_returning(), rows, execution_options=_ONE_STATEMENT
                )
                return [self._read_back(r) for r in written]
            await conn.execute(insert(self.table), rows)
        return [self.from_record(r) for r in rows]

    async def add_all_no_result(self, models: Sequence[M]) -> None:
        """Insert without reading anything back."""
        if not self._all(models):
            return
        with self._writing("BATCH_INSERT", len(models)):
            shard, rows = self._insert_rows(models)
            async with self.tx_db_else_db(shard=shard) as conn:
                await conn.execute(insert(self.table), rows)

    async def update(self, model: M) -> M:
        """Update one row under the optimistic lock, and hand back what the database holds."""
        return (await self.update_all([self._one(model)]))[0]

    async def update_no_result(self, model: M) -> None:
        await self.update_all_no_result([self._one(model)])

    async def update_all(self, models: Sequence[M]) -> list[M]:
        """Update under the optimistic lock - one statement - and read the rows back. A short
        count raises :class:`~ekbatan.errors.StaleRecordError`, as in sync."""
        written = await self._apply_updates(self._all(models), read_back=True)
        return written if written is not None else []

    async def update_all_no_result(self, models: Sequence[M]) -> None:
        """Update without reading anything back."""
        if not self._all(models):
            return
        with self._writing("BATCH_UPDATE", len(models)):
            await self._apply_updates(models, read_back=False)

    async def _apply_updates(self, models: Sequence[M], *, read_back: bool) -> list[M] | None:
        if not models:
            return [] if read_back else None
        shard, stamped = self._stamped(models)

        async with self.tx_db_else_db(shard=shard) as conn:
            returning = read_back and conn.dialect.update_returning
            stmt, binds = self._update_statement(conn.dialect.name, stamped, returning=returning)
            result = await (conn.execute(stmt) if binds is None else conn.execute(stmt, binds))
            written = self._checked(result, stamped, returning=returning)

        return self._updated(stamped, written, read_back=read_back, returning=returning)


class AsyncModelRepository[M: Model[Any, Any], R: Mapping[str, Any], K](
    AbstractAsyncRepository[M, R, K]
):
    """Rows of a :class:`~ekbatan.domain.Model`, awaited - the async
    :class:`~ekbatan.repository.ModelRepository`, held to the same declaration."""

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        super().__init__(_must_be_model(model_type), *args, **kwargs)


class AsyncEntityRepository[M: Entity[Any, Any], R: Mapping[str, Any], K](
    AbstractAsyncRepository[M, R, K]
):
    """Rows of an :class:`~ekbatan.domain.Entity`, awaited - the async
    :class:`~ekbatan.repository.EntityRepository`."""

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        super().__init__(_must_be_entity(model_type), *args, **kwargs)
