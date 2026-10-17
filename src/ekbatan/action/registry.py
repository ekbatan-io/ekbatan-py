"""Which instance runs when an action type is asked for.

An action is a singleton: one instance per type, built once at startup with the things it
needs, shared across every concurrent invocation. The registry is where that instance lives,
so a caller holding the executor can run an action without also holding the wiring::

    executor.execute(CloseWallet, WalletIdParams(wallet_id=id))

without knowing what ``CloseWallet`` was constructed with. That is the difference between an
HTTP handler that depends on the executor and one that depends on every repository its actions
happen to use.

It is also the only way in, as Java's is: the executor takes a type and runs the instance
registered for it, so an action that was never registered cannot run, and every one that runs
has been checked for reading the executor's clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Self

from ..errors import ValidationError
from .action import Action

__all__ = ["ActionRegistry", "ActionRegistryBuilder"]


class ActionRegistry:
    """The singleton instance for each action type.

    Built from a collection, or through :meth:`builder`::

        ActionRegistry([OpenWallet(), CloseWallet(wallets)])

        ActionRegistry.builder().with_action(OpenWallet()).build()
    """

    __slots__ = ("_by_type",)

    def __init__(self, actions: Iterable[Action[Any, Any]] = ()) -> None:
        self._by_type: dict[type, Action[Any, Any]] = {}
        for action in actions:
            self._add(action)

    @classmethod
    def builder(cls) -> ActionRegistryBuilder:
        return ActionRegistryBuilder()

    def _add(self, action: Action[Any, Any]) -> None:
        if isinstance(action, type):
            # The commonest way to get this wrong, and it would otherwise register the class
            # object under `type` and fail much later with nothing pointing here.
            raise ValidationError(
                f"{action.__name__} is a class, not an instance. An action is registered "
                f"already built, with whatever it needs: {action.__name__}(...)."
            )
        action_type = type(action)
        existing = self._by_type.get(action_type)
        if existing is not None and existing is not action:
            raise ValidationError(
                f"two instances of {action_type.__name__} were registered. An action is a "
                f"singleton - one instance, built with what it needs, shared by every "
                f"invocation."
            )
        self._by_type[action_type] = action

    def get[P, R](self, action_type: type[Action[P, R]]) -> Action[P, R]:
        """The instance registered for this type, or a refusal naming it."""
        try:
            return self._by_type[action_type]
        except KeyError:
            raise ValidationError(
                f"no instance of {action_type.__name__} is registered, so it cannot be run. "
                f"Register it on the ActionRegistry the executor was built with; it currently "
                f"holds {sorted(a.__name__ for a in self._by_type) or 'nothing'}."
            ) from None

    @property
    def action_types(self) -> tuple[type, ...]:
        """Every action type registered, in registration order."""
        return tuple(self._by_type)

    def __contains__(self, action_type: object) -> bool:
        return action_type in self._by_type

    def __iter__(self) -> Iterator[Action[Any, Any]]:
        return iter(self._by_type.values())

    def __len__(self) -> int:
        return len(self._by_type)

    def __repr__(self) -> str:
        return f"ActionRegistry({[a.__name__ for a in self._by_type]})"


class ActionRegistryBuilder:
    """Collects action instances, then builds the registry. Obtain via
    :meth:`ActionRegistry.builder`."""

    __slots__ = ("_actions",)

    def __init__(self) -> None:
        self._actions: list[Action[Any, Any]] = []

    def with_action(self, action: Action[Any, Any]) -> Self:
        """Register one instance, keyed by its own type.

        The type is not passed alongside it. Java needs the class token because that is how it
        dispatches; here ``type(action)`` is the same fact, and asking for it twice is asking
        for the two to disagree.
        """
        self._actions.append(action)
        return self

    def with_actions(self, actions: Iterable[Action[Any, Any]]) -> Self:
        """Register a collection - for a container that has already gathered them."""
        self._actions.extend(actions)
        return self

    def build(self) -> ActionRegistry:
        """The registry. Refuses two instances of one action type."""
        return ActionRegistry(self._actions)
