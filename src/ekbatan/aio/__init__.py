"""Native asyncio for Ekbatan - **a trial**, see ``docs/ASYNC-LEDGER.md``.

Async versions of only what waits on the database on the request path: connections and
transactions, repository reads and writes, the executor, its persister and the outbox write,
and ``perform``. Everything else - models, events, plans, builders, ids, sharding, the schema,
config, migrations - is shared with the sync API as it is, and so is every rule: the
statements, the routing, the optimistic lock, soft delete and the retry policy are one copy.

A service is all-sync or all-async. Needs an async driver: ``ekbatan[asyncpg]``.
"""

from .action import AsyncAction
from .executor import AsyncActionExecutor
from .outbox import AsyncEventPersister, AsyncSingleTableJsonEventPersister
from .persistence import (
    AsyncDatabaseRegistry,
    AsyncDb,
    AsyncTransaction,
    AsyncTransactionManager,
    async_engine_for,
)
from .persister import AsyncChangePersister
from .repository import (
    AbstractAsyncRepository,
    AsyncEntityRepository,
    AsyncModelRepository,
    AsyncRepository,
)

__all__ = [
    "AbstractAsyncRepository",
    "AsyncAction",
    "AsyncActionExecutor",
    "AsyncChangePersister",
    "AsyncDatabaseRegistry",
    "AsyncDb",
    "AsyncEntityRepository",
    "AsyncEventPersister",
    "AsyncModelRepository",
    "AsyncRepository",
    "AsyncSingleTableJsonEventPersister",
    "AsyncTransaction",
    "AsyncTransactionManager",
    "async_engine_for",
]
