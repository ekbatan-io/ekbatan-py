"""How Java's executor tests run an action they built for the case.

`ActionExecutorTest` builds an executor per test with ``actionRegistry().withAction(new X(...))``
and then calls ``execute(principal, X.class, params)``: the action is registered, and it is run
by its class. The executor only takes a class, here as there, so a test that constructs an
action with arguments of its own - which error to raise, how many attempts to count - does the
same: register it on an executor like the one it was given, and run it by type.

The executor is rebuilt through its builder, so the check that every registered action reads
the executor's clock runs here exactly as it does in production.
"""

from __future__ import annotations

from typing import Any

from ekbatan.action import ActionExecutor, ActionRegistry, ExecutionConfiguration
from ekbatan.action.action import Action


def registered(executor: ActionExecutor, *actions: Action[Any, Any]) -> ActionExecutor:
    """An executor like ``executor`` with ``actions`` registered - Java's ``executorWith(...)``."""
    return (
        ActionExecutor.builder()
        .namespace(executor.namespace)
        .database_registry(executor.registry)
        .repository_registry(executor.repositories)
        .action_registry(ActionRegistry.builder().with_actions(actions).build())
        .event_persister(executor.outbox)
        .clock(executor.clock)
        .default_execution_configuration(executor.config)
        .build()
    )


def run[P, R](
    executor: ActionExecutor,
    action: Action[P, R],
    params: P,
    *,
    principal: str | None = None,
    config: ExecutionConfiguration | None = None,
) -> R:
    """Register ``action`` and execute it by its type, in Java's order -
    ``execute(principal, type(action), params, config)``."""
    return registered(executor, action).execute(principal, type(action), params, config)
