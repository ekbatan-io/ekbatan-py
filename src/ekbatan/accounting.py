"""Counting what was sent to the database.

The framework makes a performance claim - one unit of work is one transaction, and N staged
rows is one statement rather than N - and that claim has to be checkable. Wall-clock numbers
cannot do it: they measure the machine, they differ between runtimes, and they are useless for
comparing one port against another. Statement counts measure the framework, hold everywhere,
and are exactly what an implementation loses first.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event

from .persistence import _ShardRegistry

__all__ = ["StatementCount", "count_statements"]

_WRITE = re.compile(r"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([`\"\[]?[\w.]+)", re.I)


@dataclass(slots=True)
class StatementCount:
    """What one block of work cost."""

    #: Statements sent, of any kind. A batch counts once, however many rows it carried.
    round_trips: int = 0

    #: Transactions committed, across every shard.
    transactions: int = 0

    rollbacks: int = 0

    #: Write statements per table, by its name without a schema: ``writes["events"]`` on every
    #: database, though the statement says ``eventlog.events`` on PostgreSQL and the database's
    #: own name before it elsewhere (``main.events`` on SQLite).
    writes: Counter[str] = field(default_factory=Counter)

    statements: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        writes = ", ".join(f"{t}={n}" for t, n in sorted(self.writes.items()))
        return (
            f"StatementCount(round_trips={self.round_trips}, "
            f"transactions={self.transactions}, writes={{{writes}}})"
        )


@contextmanager
def count_statements(registry: _ShardRegistry[Any]) -> Iterator[StatementCount]:
    """Count everything sent through this registry inside the block::

    with count_statements(registry) as cost:
        executor.execute(deposit, params)

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1
    assert cost.writes["events"] == 1

    A :class:`~ekbatan.persistence.DatabaseRegistry` or an ``ekbatan.aio``
    ``AsyncDatabaseRegistry``; the block may hold awaited work.
    """
    counted = StatementCount()
    sources = {registry.manager(shard).primary for shard in registry.shards}
    sources |= {registry.manager(shard).replica for shard in registry.shards}
    # An `ekbatan.aio` registry's data sources are async; SQLAlchemy fires their events on the
    # sync engine each async one wraps, so the listeners go there and the block counts awaited
    # work the same.
    engines = {
        getattr(source.sqlalchemy_engine, "sync_engine", source.sqlalchemy_engine)
        for source in sources
    }

    def before(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        counted.round_trips += 1
        counted.statements.append(statement)
        match = _WRITE.match(statement)
        if match:
            counted.writes[match.group(2).strip('`"[]').rsplit(".", 1)[-1]] += 1

    def committed(conn):  # type: ignore[no-untyped-def]
        counted.transactions += 1

    def rolled_back(conn):  # type: ignore[no-untyped-def]
        counted.rollbacks += 1

    for engine in engines:
        event.listen(engine, "before_cursor_execute", before)
        event.listen(engine, "commit", committed)
        event.listen(engine, "rollback", rolled_back)
    try:
        yield counted
    finally:
        for engine in engines:
            event.remove(engine, "before_cursor_execute", before)
            event.remove(engine, "commit", committed)
            event.remove(engine, "rollback", rolled_back)
