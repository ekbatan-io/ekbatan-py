"""Reading and writing rows, awaited.

The counterpart to :mod:`ekbatan.repository`. A repository here is the sync one's core - the
same construction checks, mapping, shard routing, soft-delete filter, statements and
optimistic-lock rules, inherited rather than copied - with the methods that run a statement
written as coroutines::

    class WalletRepository(AsyncModelRepository[Wallet, ShardedId[Wallet]]):
        def to_row(self, wallet: Wallet) -> dict[str, Any]: ...      # plain def, as in sync
        def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet: ...

        async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
            return await self.find_where(WALLETS.c.owner_id == owner_id)

It opens no transaction, as the sync one opens none: inside a unit of work it joins the one the
executor opened, outside one each statement commits itself.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import ColumnElement, Row, insert

from ..domain import Entity, Model, Persistable
from ..repository import (
    _ONE_STATEMENT,
    _must_be_entity,
    _must_be_model,
    _RepositoryCore,
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


class AbstractAsyncRepository[M: Persistable[Any, Any], K](
    _RepositoryCore[M, K], AsyncRepository[M]
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

    @asynccontextmanager
    async def readonly_db(
        self, *, key: K | None = None, shard: ShardIdentifier | None = None
    ) -> AsyncIterator[AsyncDb]:
        """The **replica**, for a read that can live with replication lag - the primary when
        the shard has none. Never joins an open transaction, as the sync one does not."""
        async with AsyncDb(self._manager(key, None, shard).replica) as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            yield conn

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

    # ------------------------------------------------------------------ reads

    async def find(self, key: K) -> M | None:
        """The row, or ``None``."""
        async with self._reader(key=key) as conn:
            row = (await conn.execute(self._by_id(key))).first()
        return self.from_row(row) if row is not None else None

    async def get(self, key: K) -> M:
        """The row, or raise."""
        return self._found(await self.find(key), key)

    async def find_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> list[M]:
        """Live rows matching, on the shard named or on every shard."""
        return [self.from_row(r) for r in await self._read(self._where(*criteria), shard)]

    async def all(self, *, shard: ShardIdentifier | None = None) -> list[M]:
        return await self.find_where(shard=shard)

    async def _read(self, stmt: Any, shard: ShardIdentifier | None) -> list[Row[Any]]:
        """Run a read on one shard, or on every shard when none is named - one after another,
        as the sync repository does."""
        shards = (shard,) if shard is not None else self._scatter_shards()
        rows: list[Row[Any]] = []
        for each in shards:
            async with self._reader(shard=each) as conn:
                rows += list(await conn.execute(stmt))
        return rows

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
                return [self.from_row(r) for r in written]
            await conn.execute(insert(self.table), rows)
        return [self.from_row(r) for r in rows]

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


class AsyncModelRepository[M: Model[Any, Any], K](AbstractAsyncRepository[M, K]):
    """Rows of a :class:`~ekbatan.domain.Model`, awaited - the async
    :class:`~ekbatan.repository.ModelRepository`, held to the same declaration."""

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        super().__init__(_must_be_model(model_type), *args, **kwargs)


class AsyncEntityRepository[M: Entity[Any, Any], K](AbstractAsyncRepository[M, K]):
    """Rows of an :class:`~ekbatan.domain.Entity`, awaited - the async
    :class:`~ekbatan.repository.EntityRepository`."""

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        super().__init__(_must_be_entity(model_type), *args, **kwargs)
