"""Keyed locks: mutual exclusion on a string, across every process that shares the backend.

The backends are Java's - Postgres, MySQL and MariaDB advisory locks, and Redis. There is no
in-process one, as there is none in Java: once an application runs as several worker processes,
as Python services do, a lock held in one process's memory excludes nothing in the others.
"""

from .base import KeyedLockProvider, Lease, lock_key_hash
from .sql import (
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
    SqlKeyedLockProvider,
    SqlKeyedLockProviderBuilder,
)

__all__ = [
    "KeyedLockProvider",
    "Lease",
    "MariaDBKeyedLockProvider",
    "MySQLKeyedLockProvider",
    "PostgresKeyedLockProvider",
    "SqlKeyedLockProvider",
    "SqlKeyedLockProviderBuilder",
    "lock_key_hash",
]
