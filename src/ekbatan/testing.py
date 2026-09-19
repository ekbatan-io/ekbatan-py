"""Running a unit of work without a database.

An action's business logic is worth testing on its own: what it stages, what it emits, what it
refuses. None of that needs a database, and a test that spins one up to find out whether a
deposit emits one event is a slow test of the wrong thing.

    staged = run_action(DepositMoney(wallets), DepositParams(...))

    assert staged.updated(Wallet)[0].balance == Decimal("150.00")
    assert [type(e).__name__ for e in staged.events] == ["WalletMoneyDeposited"]

This binds the plan through the same mechanism the executor uses, so the action sees exactly
the lifetime it sees in production - ``self.plan`` works, a plan reached from a thread the
action started still fails the way it would live, and the clock is whichever one you built the
action with.

Nothing is written and nothing is committed. When you want to know that state and events land
together, that is a test against a database, and no amount of test-support machinery is a
substitute for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .action.action import Action, _Binding
from .action.plan import ActionPlan
from .clock import Clock, SystemClock
from .domain import Model, ModelEvent, Persistable

__all__ = ["StagedWork", "run_action"]


@dataclass(frozen=True, slots=True)
class StagedWork[R]:
    """What one invocation staged, and what it returned.

    Deliberately data rather than a wall of assertion methods: Java needs twenty-four of them
    because plain assertions there say little when they fail. ``assert`` plus pytest already
    reports both sides, so these are the four accessors worth having and the rest is your test.
    """

    result: R
    plan: ActionPlan
    clock: Clock = field(default_factory=SystemClock)

    def added[P: Persistable[Any, Any]](self, model_type: type[P]) -> list[P]:
        """Rows staged for insert, of one type, in the order they were staged."""
        return list(self.plan.additions(model_type))

    def updated[P: Persistable[Any, Any]](self, model_type: type[P]) -> list[P]:
        """Rows staged for update, of one type, in the order they were staged."""
        return list(self.plan.updates(model_type))

    @property
    def additions(self) -> list[Persistable[Any, Any]]:
        """Every row staged for insert, across every type.

        Java's `assertTotalAdditions(n)`; here it is `len(staged.additions) == n`. Worth having
        as well as :meth:`added`, because a unit of work touching two types is exactly where a
        per-type count stops telling you whether anything extra was staged.
        """
        return [
            row for changes in self.plan.changes().values() for row in changes.additions.values()
        ]

    @property
    def updates(self) -> list[Persistable[Any, Any]]:
        """Every row staged for update, across every type. Java's `assertTotalUpdates(n)`."""
        staged = self.plan.changes().values()
        return [row for changes in staged for row in changes.updates.values()]

    @property
    def staged(self) -> list[Persistable[Any, Any]]:
        """Everything staged, inserts before updates within each type."""
        return [row for changes in self.plan.changes().values() for row in changes.all]

    @property
    def events(self) -> list[ModelEvent]:
        """Every event attached to a staged model, in the order the outbox would hold them.

        Only models: an entity's changes are not events, and a unit of work that stages one and
        nothing else emits nothing at all.
        """
        return [event for row in self.staged if isinstance(row, Model) for event in row.events]

    def emitted[E: ModelEvent](self, event_type: type[E]) -> list[E]:
        """The events of one type, in order."""
        return [e for e in self.events if isinstance(e, event_type)]

    def __bool__(self) -> bool:
        """Whether anything was staged at all."""
        return bool(self.plan)


def run_action[P, R](
    action: Action[P, R],
    params: P,
    *,
    principal: str | None = None,
) -> StagedWork[R]:
    """Run one unit of work against a bare plan and report what it staged.

    The plan stamps from **the action's own clock**, which is the one you handed its
    constructor. There is no override here for the same reason the executor refuses a second
    clock: a plan stamping from one instant while the action reads another is the bug this
    arrangement exists to make unwritable. Pass a ``VirtualClock`` to the action if a test
    needs a fixed instant.
    """
    plan = ActionPlan(action.clock)
    with _Binding(plan):
        result = action.perform(principal, params)
    return StagedWork(result=result, plan=plan, clock=action.clock)
