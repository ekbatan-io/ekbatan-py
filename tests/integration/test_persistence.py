"""The transaction boundary, and what happens to the connection when it goes wrong.

Java's `TransactionManagerTest`, case for case. Seven of its seventeen are about restoring
JDBC's `autoCommit` flag by hand; SQLAlchemy owns that, so what is left is the part that is a
promise in both ports - a connection whose rollback failed is in unknown state and must not be
handed to the next caller.
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy import Connection, text

ROLLBACK = "sqlalchemy.engine.base.RootTransaction.rollback"
COMMIT = "sqlalchemy.engine.base.RootTransaction.commit"


@pytest.fixture
def manager(registry):
    return registry.manager(registry.default_shard)


def _broken(target: str):
    return patch(target, side_effect=RuntimeError("the server refused"))


def _run(manager, *, fail_block=False, break_commit=False, break_rollback=False):
    """One unit of work, with any of the three steps made to fail.

    Returns what escaped the block, and whether the connection was invalidated - Java's
    `evict` - rather than released back to the pool.
    """
    escaped: BaseException | None = None
    with (
        patch.object(Connection, "invalidate", autospec=True) as invalidate,
        contextlib.ExitStack() as broken,
    ):
        if break_commit:
            broken.enter_context(_broken(COMMIT))
        if break_rollback:
            broken.enter_context(_broken(ROLLBACK))
        try:
            with manager.transaction() as tx:
                tx.execute(text("select 1"))
                if fail_block:
                    raise ValueError("the block failed")
        except BaseException as error:
            escaped = error
    return escaped, invalidate.called


def test_a_unit_of_work_that_succeeds_commits_and_keeps_its_connection(manager):
    escaped, evicted = _run(manager)

    assert escaped is None
    assert not evicted, "a healthy connection goes back to the pool"


def test_a_failing_block_rolls_back_and_keeps_its_connection(manager):
    escaped, evicted = _run(manager, fail_block=True)

    assert isinstance(escaped, ValueError)
    assert not evicted, "the rollback worked, so the connection is clean"


def test_a_failed_rollback_discards_the_connection_instead_of_returning_it(manager):
    """Java's `evicts_connection_when_rollback_itself_throws_sqlexception`.

    Returned to the pool, that connection carries whatever the rollback did not undo, and the
    next caller inherits it. SQLAlchemy on its own does return it - this is the one place the
    port has to say so itself.
    """
    escaped, evicted = _run(manager, fail_block=True, break_rollback=True)

    assert evicted, "the connection is discarded"
    assert isinstance(escaped, ValueError), (
        "and the block's own failure is what the caller sees, not the rollback's - "
        "Java rethrows `t` unchanged for the same reason"
    )


def test_a_failed_commit_is_rolled_back_like_any_other_failure(manager):
    """Java puts `commit()` inside the try, so a commit that fails takes the rollback path."""
    escaped, evicted = _run(manager, break_commit=True)

    assert escaped is not None
    assert not evicted, "the rollback after the failed commit worked"


def test_a_failed_commit_whose_rollback_also_fails_discards_the_connection(manager):
    """Java's `evicts_connection_when_commit_fails_and_rollback_also_fails`."""
    _, evicted = _run(manager, break_commit=True, break_rollback=True)

    assert evicted


def test_an_error_is_rethrown_unchanged_and_not_wrapped(manager):
    """Java's test of the same name. `BaseException`, not `Exception`: an interpreter-level
    failure is still a failed transaction, and one wrapped on the way out is one nobody's
    `except` clause will catch."""

    class Bang(BaseException):
        pass

    with pytest.raises(Bang), manager.transaction() as tx:
        tx.execute(text("select 1"))
        raise Bang


def test_the_ambient_transaction_is_visible_inside_and_gone_outside(manager):
    """Java's `exposes_dsl_context_to_current_transaction_when_block_running` and
    `current_transaction_db_context_is_empty_outside_block`."""
    assert manager.current() is None

    with manager.transaction() as tx:
        assert manager.current() is tx.connection

    assert manager.current() is None


def test_a_rolled_back_transaction_leaves_nothing_behind(manager, registry):
    """The property all of the above exists to protect."""
    table = "tx_probe"
    with manager.transaction() as tx:
        tx.execute(text(f"create table if not exists {table} (n integer)"))

    with contextlib.suppress(ValueError), manager.transaction() as tx:
        tx.execute(text(f"insert into {table} (n) values (1)"))
        raise ValueError("no")

    with manager.connection(readonly=True) as conn:
        assert conn.execute(text(f"select count(*) from {table}")).scalar() == 0

    with manager.transaction() as tx:
        tx.execute(text(f"drop table {table}"))


def test_a_nested_call_joins_rather_than_opening_a_second_transaction(manager):
    """Where the two ports genuinely differ, and deliberately.

    Java's `inTransaction` rebinds its `ScopedValue` and opens a *second* transaction on a
    *second* connection, which can block on the first one's locks. Joining is what makes "the
    unit of work commits together" true for a repository call made from inside one.
    """
    with manager.transaction() as outer, manager.transaction() as inner:
        assert inner.connection is outer.connection

    assert manager.current() is None
