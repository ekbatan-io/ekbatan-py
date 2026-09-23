"""How Java's executor tests run an action they built for the case.

`ActionExecutorTest` builds an executor per test with ``actionRegistry().withAction(new X(...))``
and then calls ``execute(principal, X.class, params)``: the action is registered, and it is run
by its class. The executor only takes a class, here as there, so a test that constructs an
action with arguments of its own - which error to raise, how many attempts to count - does the
same: register it on an executor like the one it was given, and run it by type.

The executor is rebuilt through its constructor, so the check that every registered action reads
the executor's clock runs here exactly as it does in production.
"""

from __future__ import annotations

from typing import Any

from ekbatan.action import ActionExecutor
from ekbatan.action.action import Action


def registered(executor: ActionExecutor, *actions: Action[Any, Any]) -> ActionExecutor:
    """An executor like ``executor`` with ``actions`` registered - Java's ``executorWith(...)``."""
    return ActionExecutor(
        namespace=executor.namespace,
        registry=executor.registry,
        repositories=executor.changes.repositories,
        actions=actions,
        outbox=executor.outbox,
        clock=executor.clock,
        config=executor.config,
    )


def run[P, R](executor: ActionExecutor, action: Action[P, R], params: P, **options: Any) -> R:
    """Register ``action`` and execute it by its type."""
    return registered(executor, action).execute(type(action), params, **options)
