"""Running a unit of work, and committing what it staged.

One invocation is one transaction per shard. Inside that transaction the domain rows are
written and then the events, so there is no observable moment where a change is committed and
its events are not - which is the property the whole framework exists to provide, and the only
one that is not negotiable.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Table

from ..clock import Clock, SystemClock, to_storage
from ..domain import Model, Persistable
from ..errors import CrossShardError, EkbatanError, StaleRecordError, ValidationError
from ..persistence import DatabaseRegistry
from ..repository import Repository
from ..sharding import ShardIdentifier
from .action import Action, _Binding
from .outbox import EmittedEvent, EventPersister, OutboxRecord, SingleTableOutbox
from .plan import ActionPlan

__all__ = ["ActionExecutor", "ExecutionConfig", "RetryPolicy"]

LOG = logging.getLogger("ekbatan.action")

#: Dot-separated identifiers, like a package. Anchored, so the whole value must match.
_NAMESPACE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


@dataclass(frozen=True, kw_only=True, slots=True)
class RetryPolicy:
    """When to try again.

    The default absorbs one lost race on a version check. More than that hides a real problem;
    none at all pushes an ordinary contention case into every caller.
    """

    on: type[Exception] | tuple[type[Exception], ...] = StaleRecordError
    attempts: int = 1
    delay: float = 0.1

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValidationError(f"attempts cannot be negative, got {self.attempts}")
        if self.delay < 0:
            # Caught here rather than at the first retry, which is a different request, on a
            # different day, inside a sleep with nothing pointing back at this line.
            raise ValidationError(f"delay cannot be negative, got {self.delay}")

    def matches(self, error: BaseException) -> bool:
        return isinstance(error, self.on)


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecutionConfig:
    """Per-call policy."""

    retry: RetryPolicy | None = field(default_factory=RetryPolicy)
    allow_cross_shard: bool = False

    @classmethod
    def no_retry(cls, **kwargs: Any) -> ExecutionConfig:
        return cls(retry=None, **kwargs)


DEFAULT = ExecutionConfig()


class ActionExecutor:
    """Runs units of work.

    Actions are passed as instances, not looked up by name. An action is an object you built
    with the things it needs; a registry between the caller and it would buy indirection and
    one more place for a name to be wrong.
    """

    __slots__ = ("_by_model", "clock", "config", "namespace", "outbox", "registry")

    def __init__(
        self,
        *,
        namespace: str,
        registry: DatabaseRegistry,
        repositories: Iterable[Repository[Any, Any]],
        events: Table | None = None,
        outbox: EventPersister | None = None,
        clock: Clock | None = None,
        config: ExecutionConfig = DEFAULT,
    ) -> None:
        self.namespace = _valid_namespace(namespace)
        self.registry = registry
        self.clock = clock or SystemClock()
        self.config = config

        self._by_model: dict[type, Repository[Any, Any]] = {}
        for repo in repositories:
            self._by_model[repo.model_type] = repo
        if not self._by_model:
            raise ValidationError("at least one repository is required")

        if outbox is not None:
            self.outbox: EventPersister = outbox
        elif events is not None:
            self.outbox = SingleTableOutbox(events)
        else:
            raise ValidationError("pass either events=<Table> or outbox=<EventPersister>")

    # ------------------------------------------------------------------ running

    def execute[P, R](
        self,
        action: Action[P, R],
        params: P,
        *,
        principal: str | None = None,
        config: ExecutionConfig | None = None,
    ) -> R:
        """Run one unit of work and commit what it staged."""
        policy = config or self.config
        name = action.name
        attempts = 1 + (policy.retry.attempts if policy.retry else 0)

        for attempt in range(1, attempts + 1):
            started = to_storage(self.clock.now())
            # A fresh plan per attempt. If it survived a failed one, the retry would commit the
            # first attempt's staged changes on top of its own - two deposits for one call.
            plan = ActionPlan(self.clock)
            try:
                with _Binding(plan, principal):
                    result = action.perform(params)
                self._commit(name, plan, params, started, policy)
                return result
            except Exception as error:
                retryable = policy.retry is not None and policy.retry.matches(error)
                if retryable and attempt < attempts:
                    LOG.info(
                        "%s: %s on attempt %d/%d, retrying in %.0fms",
                        name,
                        type(error).__name__,
                        attempt,
                        attempts,
                        policy.retry.delay * 1000,  # type: ignore[union-attr]
                    )
                    time.sleep(policy.retry.delay)  # type: ignore[union-attr]
                    continue
                LOG.error("%s failed: %s: %s", name, type(error).__name__, error)
                raise

        raise EkbatanError("unreachable")  # pragma: no cover

    # ---------------------------------------------------------------- committing

    def _commit(
        self,
        action_name: str,
        plan: ActionPlan,
        params: Any,
        started: Any,
        policy: ExecutionConfig,
    ) -> None:
        by_shard = self._group_by_shard(plan)

        if len(by_shard) > 1 and not policy.allow_cross_shard:
            # Refused before anything is written. Committing per shard by default would turn
            # the central promise into a lie the first time an action reached two databases by
            # accident - and it would do it silently.
            raise CrossShardError(sorted(str(s) for s in by_shard))

        if not by_shard:
            # Nothing was staged, and the invocation is still recorded. See SingleTableOutbox.
            by_shard = {self.registry.default_shard: {}}

        action_id = uuid.uuid4()
        committed: list[ShardIdentifier] = []

        for shard, slices in by_shard.items():
            try:
                self._commit_shard(shard, slices, action_name, action_id, params, started)
                committed.append(shard)
            except Exception as error:
                if committed:
                    # The documented consequence of opting in, and the only notice anyone gets.
                    LOG.critical(
                        "%s PARTIALLY COMMITTED: shards %s are committed and will not roll "
                        "back; shard %s failed with %s: %s",
                        action_name,
                        [str(s) for s in committed],
                        shard,
                        type(error).__name__,
                        error,
                    )
                raise

        if len(by_shard) > 1:
            LOG.warning(
                "%s spans %d shards %s; each committed on its own",
                action_name,
                len(by_shard),
                [str(s) for s in by_shard],
            )

    def _commit_shard(
        self,
        shard: ShardIdentifier,
        slices: Mapping[type, tuple[list[Any], list[Any]]],
        action_name: str,
        action_id: uuid.UUID,
        params: Any,
        started: Any,
    ) -> None:
        with self.registry.manager(shard).transaction():
            events: list[EmittedEvent] = []
            for model_type, (additions, updates) in slices.items():
                repo = self._repository(model_type)
                if issubclass(model_type, Model):
                    events += _events_of(additions)
                    events += _events_of(updates)
                # The no-result variants deliberately. The plan already holds the values that
                # were staged and the events came off those; nothing here reads a written row
                # back, so asking the database to return every one of them would be paid on
                # every unit of work for nothing.
                if additions:
                    repo.add_all_no_result(additions)
                if updates:
                    repo.update_all_no_result(updates)

            self.outbox.persist(
                _connection(self.registry, shard),
                OutboxRecord(
                    namespace=self.namespace,
                    action_id=action_id,
                    action_name=action_name,
                    action_params=params,
                    started_date=started,
                    completion_date=to_storage(self.clock.now()),
                    events=events,
                    shard=shard,
                ),
            )

    def _group_by_shard(
        self, plan: ActionPlan
    ) -> dict[ShardIdentifier, dict[type, tuple[list[Any], list[Any]]]]:
        grouped: dict[ShardIdentifier, dict[type, tuple[list[Any], list[Any]]]] = {}
        for model_type, changes in plan.changes().items():
            if not changes:
                continue
            repo = self._repository(model_type)
            for staged, index in ((changes.additions, 0), (changes.updates, 1)):
                for model in staged.values():
                    shard = repo.shard_for(model.id)
                    slot = grouped.setdefault(shard, {}).setdefault(model_type, ([], []))
                    slot[index].append(model)
        return grouped

    def _repository(self, model_type: type) -> Repository[Any, Any]:
        try:
            return self._by_model[model_type]
        except KeyError:
            raise ValidationError(
                f"no repository was given for {model_type.__name__}, so the change staged for "
                f"it cannot be written. Pass one to ActionExecutor(repositories=[...])."
            ) from None


def _events_of(models: Sequence[Persistable[Any, Any]]) -> list[EmittedEvent]:
    return [
        EmittedEvent(str(model.id), type(model).__name__, event)
        for model in models
        for event in getattr(model, "events", ())
    ]


def _connection(registry: DatabaseRegistry, shard: ShardIdentifier) -> Any:
    """The connection of the transaction already open on this shard.

    The outbox write has to land in it. A store handed its own connection would commit
    separately, which is the dual write this framework exists to remove.
    """
    from ..persistence import _open

    conn = _open().get(shard)
    if conn is None:  # pragma: no cover - _commit_shard has already opened one
        raise EkbatanError(f"no open transaction on shard {shard}")
    return conn


def _valid_namespace(namespace: str) -> str:
    """The namespace is a naming root, not a label.

    It reaches topic names and the package a payload's schema declares, and a hyphen is a
    syntax error in a protobuf package - ``package my-service;`` fails in protoc, not in
    review. Rejected rather than rewritten: turning ``my-service`` into ``my_service`` would
    silently change the topic name and every stored row.
    """
    if not namespace or not namespace.strip():
        raise ValidationError("namespace is required")
    if not _NAMESPACE.match(namespace):
        raise ValidationError(
            f"namespace {namespace!r} must be dot-separated identifiers, like a package: "
            f"letters, digits and underscore, each segment starting with a letter or "
            f"underscore. Hyphens are not legal in a protobuf package or an Avro namespace, "
            f"and this value is used as one - write 'my_service' rather than 'my-service'."
        )
    return namespace
