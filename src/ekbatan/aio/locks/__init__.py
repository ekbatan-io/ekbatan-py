"""Keyed locks for code on an event loop: the sync providers' backends and rules, awaited.

The caller takes the lock around the unit of work, as on the sync side::

    async with locks.acquire(f"wallet:{wallet_id}", timedelta(seconds=30)):
        wallet = await executor.execute(principal, WalletDepositMoneyAction, params)

The holder is the task. Same backends as ``ekbatan.locks`` - PostgreSQL advisory locks, MySQL and
MariaDB ``GET_LOCK``, and Redis in ``ekbatan.aio.locks.redis`` - holding the same keys, so an async
service, a sync one and a Java one contend for one lock.
"""

from .base import AsyncKeyedLockProvider, AsyncLease
from .sql import (
    AsyncMariaDBKeyedLockProvider,
    AsyncMySQLKeyedLockProvider,
    AsyncPostgresKeyedLockProvider,
    AsyncSqlKeyedLockProvider,
    AsyncSqlKeyedLockProviderBuilder,
)

__all__ = [
    "AsyncKeyedLockProvider",
    "AsyncLease",
    "AsyncMariaDBKeyedLockProvider",
    "AsyncMySQLKeyedLockProvider",
    "AsyncPostgresKeyedLockProvider",
    "AsyncSqlKeyedLockProvider",
    "AsyncSqlKeyedLockProviderBuilder",
]
