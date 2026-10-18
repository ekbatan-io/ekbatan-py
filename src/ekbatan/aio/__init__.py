"""Native asyncio for Ekbatan.

Async versions of only what waits on the database on the request path: connections and
transactions, repository reads and writes, the executor, its persister and the outbox write,
and ``perform``. Everything else - models, events, plans, builders, ids, sharding, the schema,
config, migrations - is shared with the sync API as it is, and so is every rule: the
statements, the routing, the optimistic lock, soft delete and the retry policy are one copy.

A service is all-sync or all-async on its request path. Event handlers, distributed jobs and
keyed locks stay sync, on threads, beside it. Needs an async driver: the ``postgres-async``,
``mysql-async``, ``mariadb-async`` or ``sqlite-async`` extra.
"""

from .action import AsyncAction
from .executor import AsyncActionExecutor, AsyncActionExecutorBuilder
from .outbox import AsyncEventPersister, AsyncSingleTableJsonEventPersister
from .persistence import (
    ASYNC_DEFAULT_DRIVERS,
    AsyncDatabaseRegistry,
    AsyncDatabaseRegistryBuilder,
    AsyncDb,
    AsyncTransaction,
    AsyncTransactionManager,
    async_engine_for,
    async_url,
)
from .persister import AsyncChangePersister
from .repository import (
    AbstractAsyncRepository,
    AsyncEntityRepository,
    AsyncModelRepository,
    AsyncRepository,
)
from .testing import AsyncActionSpec, async_run_action

__all__ = [
    "ASYNC_DEFAULT_DRIVERS",
    "AbstractAsyncRepository",
    "AsyncAction",
    "AsyncActionExecutor",
    "AsyncActionExecutorBuilder",
    "AsyncActionSpec",
    "AsyncChangePersister",
    "AsyncDatabaseRegistry",
    "AsyncDatabaseRegistryBuilder",
    "AsyncDb",
    "AsyncEntityRepository",
    "AsyncEventPersister",
    "AsyncModelRepository",
    "AsyncRepository",
    "AsyncSingleTableJsonEventPersister",
    "AsyncTransaction",
    "AsyncTransactionManager",
    "async_engine_for",
    "async_run_action",
    "async_url",
]
