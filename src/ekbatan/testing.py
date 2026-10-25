"""Running a unit of work without a database.

An action's business logic is worth testing on its own: what it stages, what it emits, what it
refuses. None of that needs a database, and a test that spins one up to find out whether a
deposit emits one event is a slow test of the wrong thing.

    (ActionSpec.of(WalletDepositMoneyAction(clock, wallet_repository))
        .execute(WalletDepositMoneyAction.Params(...))
        .assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
        .assert_emitted(WalletMoneyDepositedEvent, 1))

:class:`ActionSpec` and :class:`ActionAssert` are Java's, method for method. :func:`run_action`
hands back the same information as data, for a test that reads better as plain ``assert``::

    staged = run_action(
        None,
        WalletDepositMoneyAction(clock, wallet_repository),
        WalletDepositMoneyAction.Params(...),
    )

    assert staged.updated(Wallet)[0].balance == Decimal("150.00")
    assert [type(e).__name__ for e in staged.events] == ["WalletMoneyDepositedEvent"]

Both bind the plan through the same mechanism the executor uses, so the action sees exactly
the lifetime it sees in production - ``self.plan`` works, a plan reached from a thread the
action started still fails the way it would live, and the clock is whichever one you built the
action with.

Nothing is written and nothing is committed. When you want to know that state and events land
together, that is a test against a database, and no amount of test-support machinery is a
substitute for it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self, overload

from .action.action import Action, _ActionCore, _Binding
from .action.plan import ActionPlan
from .clock import Clock, SystemClock
from .domain import Model, ModelEvent, Persistable

__all__ = ["ActionAssert", "ActionSpec", "StagedWork", "run_action"]


@dataclass(frozen=True, slots=True)
class StagedWork[R]:
    """What one invocation staged, and what it returned.

    The data underneath :class:`ActionAssert`. Use this directly when plain ``assert`` reads
    better than a chain; use :class:`ActionSpec` for Java's chain, method for method.
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
    def events(self) -> list[ModelEvent[Any]]:
        """Every event attached to a staged model, in the order the outbox would hold them.

        Only models: an entity's changes are not events, and a unit of work that stages one and
        nothing else emits nothing at all.
        """
        return [event for row in self.staged if isinstance(row, Model) for event in row.events]

    def emitted[E: ModelEvent[Any]](self, event_type: type[E]) -> list[E]:
        """The events of one type, in order."""
        return [e for e in self.events if isinstance(e, event_type)]

    def __bool__(self) -> bool:
        """Whether anything was staged at all."""
        return bool(self.plan)


def run_action[P, R](
    principal: str | None,
    action: Action[P, R],
    params: P,
    /,
) -> StagedWork[R]:
    """Run one unit of work against a bare plan and report what it staged.

    The principal first, as :meth:`~ekbatan.action.ActionExecutor.execute` and ``perform`` take
    it - Java's order. A call in the old order, the action first, is refused naming this one.

    The plan stamps from **the action's own clock**, which is the one you handed its
    constructor. There is no override here for the same reason the executor refuses a second
    clock: a plan stamping from one instant while the action reads another is the bug this
    arrangement exists to make unwritable. Pass a ``VirtualClock`` to the action if a test
    needs a fixed instant.
    """
    _principal_first("run_action", principal, action)
    plan = ActionPlan(action.clock)
    with _Binding(plan):
        result = action.perform(principal, params)
    return StagedWork(result=result, plan=plan, clock=action.clock)


def _principal_first(helper: str, principal: object, action: object) -> None:
    """Refuse a call in the old order - the action first - naming Java's."""
    if (principal is not None and not isinstance(principal, str)) or not isinstance(
        action, _ActionCore
    ):
        raise TypeError(
            f"{helper} takes the principal first, as execute does - "
            f"{helper}(principal, action, params), the principal a str or None and the action an "
            f"instance - not a {type(principal).__name__} and then a {type(action).__name__}"
        )


# -- Java's ActionSpec / ActionAssert, method for method ----------------------------------------


def _check[T](verifier: Callable[[T], object], value: T, what: str) -> None:
    """Run one verifier, and refuse to let it pass by accident.

    Java's verifier is a ``Consumer``: it asserts inside itself and returns nothing, so there is
    no return value to ignore. A Python ``lambda`` cannot contain an ``assert`` statement, so the
    natural spelling - ``lambda w: w.balance == Decimal("150")`` - *computes* a verdict and
    returns it. Ported literally, that verdict would be thrown away and the test would pass
    whatever the balance was.

    So a verifier may do either. One that asserts inside itself and returns ``None`` passes or
    fails on its own assertions, as Java's does. One that returns a value fails if the value is
    falsy - ``False``, but also ``0``, ``""`` or an empty list, because a verdict that is not
    ``True`` is not a pass.
    """
    verdict = verifier(value)
    if verdict is not None and not verdict:
        raise AssertionError(
            f"{what} did not satisfy the verifier (it returned {verdict!r}): {value!r}"
        )


class ActionAssert[R]:
    """Java's ``ActionAssert``: chainable checks over what one invocation staged.

    Every method has Java's name in snake case, Java's overloads as optional arguments, and
    Java's failure message word for word. Additions and updates match the **exact** class, as
    Java's ``plan.changes().get(type)`` does; events match subclasses too, as ``isInstance``
    does.
    """

    __slots__ = ("_staged",)

    def __init__(self, staged: StagedWork[R]) -> None:
        self._staged = staged

    # -- additions ------------------------------------------------------------------------------

    @overload
    def assert_added[E](self, model_type: type[E], verifier: Callable[[E], object], /) -> Self: ...
    @overload
    def assert_added[E](
        self,
        model_type: type[E],
        count: int,
        verifier: Callable[[list[E]], object] | None = None,
        /,
    ) -> Self: ...
    def assert_added(self, model_type: type[Any], expected: Any, verifier: Any = None, /) -> Self:
        """``(type, verifier)``: exactly one, handed to the verifier. ``(type, count)``: that
        many. ``(type, count, verifier)``: that many, handed over as a list."""
        return self._staged_as(
            "addition", self.additions(model_type), model_type, expected, verifier
        )

    def assert_no_additions(self) -> Self:
        total = len(self._staged.additions)
        if total > 0:
            raise AssertionError(f"Expected no additions but found {total}")
        return self

    def assert_no_additions_of(self, model_type: type[Any]) -> Self:
        added = self.additions(model_type)
        if added:
            raise AssertionError(
                f"Expected no additions of {model_type.__name__} but found {len(added)}"
            )
        return self

    def assert_total_additions(self, count: int) -> Self:
        total = len(self._staged.additions)
        if total != count:
            raise AssertionError(f"Expected {count} total addition(s) but found {total}")
        return self

    # -- updates --------------------------------------------------------------------------------

    @overload
    def assert_updated[E](
        self, model_type: type[E], verifier: Callable[[E], object], /
    ) -> Self: ...
    @overload
    def assert_updated[E](
        self,
        model_type: type[E],
        count: int,
        verifier: Callable[[list[E]], object] | None = None,
        /,
    ) -> Self: ...
    def assert_updated(self, model_type: type[Any], expected: Any, verifier: Any = None, /) -> Self:
        """As :meth:`assert_added`, for updates."""
        return self._staged_as("update", self.updates(model_type), model_type, expected, verifier)

    def assert_no_updates(self) -> Self:
        total = len(self._staged.updates)
        if total > 0:
            raise AssertionError(f"Expected no updates but found {total}")
        return self

    def assert_no_updates_of(self, model_type: type[Any]) -> Self:
        updated = self.updates(model_type)
        if updated:
            raise AssertionError(
                f"Expected no updates of {model_type.__name__} but found {len(updated)}"
            )
        return self

    def assert_total_updates(self, count: int) -> Self:
        total = len(self._staged.updates)
        if total != count:
            raise AssertionError(f"Expected {count} total update(s) but found {total}")
        return self

    # -- events ---------------------------------------------------------------------------------

    @overload
    def assert_emitted[E: ModelEvent[Any]](self, event_type: type[E], /) -> Self: ...
    @overload
    def assert_emitted[E: ModelEvent[Any]](
        self, event_type: type[E], verifier: Callable[[E], object], /
    ) -> Self: ...
    @overload
    def assert_emitted[E: ModelEvent[Any]](
        self,
        event_type: type[E],
        count: int,
        verifier: Callable[[list[E]], object] | None = None,
        /,
    ) -> Self: ...
    def assert_emitted(
        self, event_type: type[Any], expected: Any = None, verifier: Any = None, /
    ) -> Self:
        """``(type)``: at least one. ``(type, verifier)``: exactly one, handed to the verifier.
        ``(type, count)`` and ``(type, count, verifier)``: that many, as a list."""
        events = self._staged.emitted(event_type)
        name = event_type.__name__
        if expected is None:
            if not events:
                raise AssertionError(f"Expected at least 1 event of {name} but none were emitted")
            return self
        if callable(expected):
            if len(events) != 1:
                raise AssertionError(f"Expected exactly 1 event of {name} but found {len(events)}")
            _check(expected, events[0], f"the {name} emitted")
            return self
        if len(events) != expected:
            raise AssertionError(f"Expected {expected} event(s) of {name} but found {len(events)}")
        if verifier is not None:
            _check(verifier, events, f"the {name} events emitted")
        return self

    def assert_not_emitted(self, event_type: type[ModelEvent[Any]]) -> Self:
        events = self._staged.emitted(event_type)
        if events:
            raise AssertionError(
                f"Expected no events of {event_type.__name__} but found {len(events)}"
            )
        return self

    def assert_no_events(self) -> Self:
        total = len(self._staged.events)
        if total > 0:
            raise AssertionError(f"Expected no events but found {total}")
        return self

    # -- no changes -----------------------------------------------------------------------------

    def assert_no_changes(self) -> Self:
        return self.assert_no_additions().assert_no_updates().assert_no_events()

    def assert_no_changes_of(self, model_type: type[Any]) -> Self:
        return self.assert_no_additions_of(model_type).assert_no_updates_of(model_type)

    # -- result ---------------------------------------------------------------------------------

    def assert_result(self, verifier: Callable[[R], object]) -> Self:
        _check(verifier, self._staged.result, "the result")
        return self

    # -- raw accessors --------------------------------------------------------------------------

    def result(self) -> R:
        return self._staged.result

    def additions[E](self, model_type: type[E]) -> list[E]:
        return list(self._staged.plan.additions(model_type))  # type: ignore[type-var]

    def updates[E](self, model_type: type[E]) -> list[E]:
        return list(self._staged.plan.updates(model_type))  # type: ignore[type-var]

    # -- shared ---------------------------------------------------------------------------------

    def _staged_as(
        self, kind: str, rows: list[Any], model_type: type[Any], expected: Any, verifier: Any
    ) -> Self:
        name = model_type.__name__
        if callable(expected):
            if len(rows) != 1:
                raise AssertionError(f"Expected exactly 1 {kind} of {name} but found {len(rows)}")
            _check(expected, rows[0], f"the {name} {kind}")
            return self
        if len(rows) != expected:
            raise AssertionError(f"Expected {expected} {kind}(s) of {name} but found {len(rows)}")
        if verifier is not None:
            _check(verifier, rows, f"the {name} {kind}s")
        return self


class ActionSpec[P, R]:
    """Java's ``ActionSpec``: run one action against a bare plan, then assert on it.

    ::

        (ActionSpec.of(WalletDepositMoneyAction(clock, wallet_repository))
            .with_principal("alice")
            .execute(WalletDepositMoneyAction.Params(wallet_id=wallet_id, amount=Decimal("50")))
            .assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
            .assert_emitted(WalletMoneyDepositedEvent, 1)
            .assert_no_additions())

    Runs exactly as :func:`run_action` does - the plan bound through the executor's own
    mechanism, stamping from the action's clock - so nothing here behaves differently from
    production except that nothing is written.
    """

    __slots__ = ("_action", "_principal")

    def __init__(self, action: Action[P, R]) -> None:
        self._action = action
        self._principal: str | None = None

    @classmethod
    def of(cls, action: Action[P, R]) -> ActionSpec[P, R]:
        return cls(action)

    def with_principal(self, principal: str | None) -> Self:
        self._principal = principal
        return self

    def execute(self, params: P) -> ActionAssert[R]:
        return ActionAssert(run_action(self._principal, self._action, params))

    def assert_throws[E: Exception](
        self,
        exception_type: type[E],
        params: P,
        verifier: Callable[[E], object] | None = None,
    ) -> Self:
        """The action raises ``exception_type`` - or a subclass, as ``isInstance`` allows.

        ``try`` / ``except`` / ``else`` rather than Java's shape, because in Python
        ``AssertionError`` *is* an ``Exception``: raising "nothing was thrown" inside the ``try``
        would be caught by the very ``except`` meant for the action's own error.
        """
        name = exception_type.__name__
        try:
            run_action(self._principal, self._action, params)
        except Exception as error:
            if not isinstance(error, exception_type):
                raise AssertionError(f"Expected {name} but got {type(error).__name__}") from error
            if verifier is not None:
                _check(verifier, error, f"the {name} raised")
        else:
            raise AssertionError(f"Expected {name} to be thrown but nothing was thrown")
        return self
