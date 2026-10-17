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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy import Table

from .._tracing import span
from ..clock import Clock, SystemClock, to_instant
from ..errors import CrossShardError, StaleRecordError, ValidationError
from ..persistence import DatabaseRegistry
from ..repository import Repository, RepositoryRegistry
from ..sharding import ShardIdentifier
from .action import Action, _Binding
from .outbox import EventPersister, SingleTableJsonEventPersister
from .persister import ChangePersister
from .plan import ActionPlan, PersistableChanges
from .registry import ActionRegistry

__all__ = [
    "ActionExecutor",
    "ActionExecutorBuilder",
    "ExecutionConfiguration",
    "ExecutionConfigurationBuilder",
    "RetryConfig",
]

LOG = logging.getLogger("io.ekbatan.core.action.ActionExecutor")
# Java logs a retry from its `Retry` class, under that class's logger.
RETRY_LOG = logging.getLogger("io.ekbatan.core.action.Retry")

#: Dot-separated identifiers, like a package. Anchored, so the whole value must match.
_NAMESPACE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


@dataclass(frozen=True, kw_only=True, slots=True)
class RetryConfig:
    """When to try again, for one class of failure.

    A configuration holds several of these, because the answers genuinely differ: a lost
    version race deserves one quick retry, an unreachable lock service deserves a few slower
    ones, and a validation error deserves none at all. One policy for everything has to pick
    the wrong number for something.
    """

    on: type[BaseException] | tuple[type[BaseException], ...] = StaleRecordError
    #: How many times to try *again*, so one run plus this many - Java's `maxRetries`. Named
    #: for what it counts: `attempts=1` reading as one attempt and running twice is the sort of
    #: thing nobody catches in review and everybody hits in production.
    max_retries: int = 1
    delay: float = 0.1

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValidationError(f"max_retries cannot be negative, got {self.max_retries}")
        if self.delay < 0:
            # Caught here rather than at the first retry, which is a different request, on a
            # different day, inside a sleep with nothing pointing back at this line.
            raise ValidationError(f"delay cannot be negative, got {self.delay}")

    def matches(self, error: BaseException) -> bool:
        """The exact class, as Java's ``retryConfigs.get(current.getClass())`` is.

        Not ``isinstance``, which is the reflex here and was this port's rule until
        `EKB-0198`. Subclass matching retries strictly more than Java does for the same
        configuration, and which failures retry is not a thing to differ on quietly.

        The cost is real and worth naming: a policy `on` a class that is only ever raised as
        one of its subclasses matches nothing, silently. Name the class that is thrown.
        """
        exact = self.on if isinstance(self.on, tuple) else (self.on,)
        return type(error) in exact


#: One quick retry on a lost version race, and nothing else. Enough to absorb ordinary
#: contention; not enough to hide a real problem.
DEFAULT_RETRIES: tuple[RetryConfig, ...] = (RetryConfig(),)


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecutionConfiguration:
    """Per-call policy."""

    #: Java's `retryConfigs`, as an ordered tuple rather than a map keyed by exception class -
    #: the class it would be keyed by lives on each config as `on`, which can name several.
    #: Tried in order; the first whose `on` matches wins. Order is the only precedence rule,
    #: so a narrow policy goes before a broad one.
    retry_configs: tuple[RetryConfig, ...] = DEFAULT_RETRIES
    allow_cross_shard: bool = False

    @classmethod
    def no_retry(cls, **kwargs: Any) -> ExecutionConfiguration:
        """A failure reaches the caller unchanged.

        For a unit of work whose side effects are not safe to repeat - and the caller is the
        only one who knows that.
        """
        return cls(retry_configs=(), **kwargs)

    @classmethod
    def builder(cls) -> ExecutionConfigurationBuilder:
        return ExecutionConfigurationBuilder()

    def policy_for(self, error: BaseException) -> tuple[RetryConfig, BaseException] | None:
        """The policy for this failure, looking through what it was raised from.

        A driver wrapping a conflict would otherwise be unrecognisable - the retryable failure
        is there, one link down. ``__cause__`` only, which is exactly what Java's ``getCause()``
        walk reaches: an explicit ``raise … from …`` and nothing else.

        Until `EKB-0198` this also followed ``__context__`` - the error that happened to be
        being handled when this one was raised. Python records that for free and Java has no
        equivalent, so following it retried failures Java would not.
        """
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            for policy in self.retry_configs:
                if policy.matches(current):
                    return policy, current
            current = current.__cause__
        return None


DEFAULT = ExecutionConfiguration()


class ExecutionConfigurationBuilder:
    """Fluent builder for :class:`ExecutionConfiguration`. Obtain via
    :meth:`ExecutionConfiguration.builder`.

    Starts from the same one retry on a lost version race that Java's builder starts from.
    """

    __slots__ = ("_allow_cross_shard", "_retry_configs")

    def __init__(self) -> None:
        self._retry_configs: list[RetryConfig] = list(DEFAULT_RETRIES)
        self._allow_cross_shard = False

    def with_retry(self, config: RetryConfig) -> Self:
        """Add one policy, or replace the one already held for the same ``on``.

        Java's ``withRetry(exceptionType, config)`` puts into a map keyed by the class, so a
        second call for ``StaleRecordException`` replaces the default rather than sitting
        behind it. The class is on the config here, and replacing by it keeps that rule - an
        appended policy for the same class would never be reached, since the first match wins.
        """
        for i, held in enumerate(self._retry_configs):
            if held.on == config.on:
                self._retry_configs[i] = config
                return self
        self._retry_configs.append(config)
        return self

    def retry_configs(self, retry_configs: Iterable[RetryConfig]) -> Self:
        """Replace every policy."""
        self._retry_configs = list(retry_configs)
        return self

    def no_retry(self) -> Self:
        """Drop every policy, the default included."""
        self._retry_configs.clear()
        return self

    def allow_cross_shard(self, allow_cross_shard: bool) -> Self:
        self._allow_cross_shard = allow_cross_shard
        return self

    def build(self) -> ExecutionConfiguration:
        return ExecutionConfiguration(
            retry_configs=tuple(self._retry_configs), allow_cross_shard=self._allow_cross_shard
        )


class ActionExecutor:
    """Runs units of work.

    An action is named by its type and found in the executor's
    :class:`~ekbatan.action.registry.ActionRegistry`, as Java's is by its class::

        executor.execute(CloseWallet, WalletIdParams(wallet_id=id))

    So a caller holding the executor runs an action without holding what it was built with, and
    everything a service can do is listed in one place. An instance is refused: it would run
    whether it was registered or not, and past the check that it reads this executor's clock. A
    test that wants to run the object it just built uses
    :class:`~ekbatan.testing.ActionSpec`, as Java's tests do.
    """

    __slots__ = ("actions", "changes", "clock", "config", "namespace", "outbox", "registry")

    def __init__(
        self,
        *,
        namespace: str,
        registry: DatabaseRegistry,
        repositories: RepositoryRegistry | Iterable[Repository[Any]],
        actions: ActionRegistry | Iterable[Action[Any, Any]] = (),
        events: Table | None = None,
        outbox: EventPersister | None = None,
        clock: Clock | None = None,
        config: ExecutionConfiguration = DEFAULT,
    ) -> None:
        self.namespace = _valid_namespace(namespace)
        # Named here, as Java names them in its builder. `registry=None` was accepted and the
        # executor built fine; the first unit of work then failed somewhere inside routing,
        # naming a shard lookup rather than the argument that was never passed.
        if registry is None:
            raise ValidationError("registry is required")
        if repositories is None:
            raise ValidationError("repositories is required")
        self.registry = registry
        self.clock = clock or SystemClock()
        self.config = config
        self.actions = actions if isinstance(actions, ActionRegistry) else ActionRegistry(actions)

        if outbox is not None:
            self.outbox: EventPersister = outbox
        elif events is not None:
            self.outbox = SingleTableJsonEventPersister(registry, events)
        else:
            raise ValidationError("pass either events=<Table> or outbox=<EventPersister>")

        self.changes = ChangePersister(
            repositories
            if isinstance(repositories, RepositoryRegistry)
            else RepositoryRegistry(repositories),
            self.outbox,
        )
        self._refuse_a_second_clock()

    def _refuse_a_second_clock(self) -> None:
        """Every registered action reads the clock this executor stamps rows with.

        The clock is a constructor field on the action, as it is in Java, where a container
        hands the same bean to both. There is no container here, so the two are wired by hand
        and can be wired differently - and the failure that produces is a test that froze the
        executor's clock, asserted on a timestamp the action took from the system clock, and
        passed on the machine it was written on. Checked once, here, where both are in hand.
        """
        wrong = [action for action in self.actions if action.clock is not self.clock]
        if wrong:
            named = ", ".join(sorted(type(a).__name__ for a in wrong))
            raise ValidationError(
                f"{named} was built with a different clock than this ActionExecutor. Pass the "
                f"executor's clock to every action it runs - a unit of work reading one clock "
                f"while its rows are stamped from another cannot be tested at a fixed instant."
            )

    @classmethod
    def builder(cls) -> ActionExecutorBuilder:
        return ActionExecutorBuilder()

    @property
    def repositories(self) -> RepositoryRegistry:
        """The registry the executor writes through."""
        return self.changes.repositories

    # ------------------------------------------------------------------ running

    def execute[P, R](
        self,
        action: type[Action[P, R]],
        params: P,
        *,
        principal: str | None = None,
        config: ExecutionConfiguration | None = None,
    ) -> R:
        """Run one unit of work and commit what it staged.

        Java's ``execute(principal, DepositMoneyAction.class, params)``: the action is named by
        its type, and the registered instance is the one that runs.
        """
        if action is None:
            raise ValidationError("an action type is required, registered with the executor")
        if not isinstance(action, type):
            raise ValidationError(
                f"execute takes the action's type, as Java's takes its class - pass "
                f"{type(action).__name__}, not an instance of it, and register the instance with "
                f"the executor's ActionRegistry. An instance would run unregistered, and past the "
                f"check that it reads this executor's clock. To run one you just built in a test, "
                f"use ActionSpec.of(action)."
            )
        running = self.actions.get(action)
        policy = config or self.config
        name = running.name
        retried = 0

        with span(
            "ekbatan.action.execute",
            {"ekbatan.action.name": name, "ekbatan.action.principal": principal or ""},
        ) as execution:
            started_at = time.monotonic()
            LOG.info("executing %s [principal=%s]", name, principal or "")
            while True:
                started = to_instant(self.clock.now())
                # A fresh plan per attempt. If it survived a failed one, the retry would commit
                # the first attempt's staged changes on top of its own - two deposits for one
                # call.
                plan = ActionPlan(self.clock)
                try:
                    with span("ekbatan.action.perform"), _Binding(plan):
                        result = running.perform(principal, params)
                    self._commit(name, plan, params, started, policy)
                except BaseException as error:
                    # BaseException, not Exception. Something that is not an ordinary error -
                    # a cancellation, a failure the runtime raises - still failed this unit of
                    # work, and catching only Exception meant it was recorded as neither a
                    # success nor a failure: no log line, no span status, nothing.
                    match = policy.policy_for(error) if isinstance(error, Exception) else None
                    if match is not None and retried < match[0].max_retries:
                        retried += 1
                        chosen, cause = match
                        RETRY_LOG.warning(
                            "retrying %s (%d/%d) after %s: %s",
                            name,
                            retried,
                            chosen.max_retries,
                            type(cause).__name__,
                            cause,
                        )
                        execution.set({"ekbatan.action.retry.count": retried})
                        execution.event(
                            "retry",
                            {"retry.count": retried, "retry.exception": type(cause).__name__},
                        )
                        time.sleep(chosen.delay)
                        continue
                    execution.failed(error, outcome="ekbatan.action.outcome")
                    LOG.error("%s failed: %s: %s", name, type(error).__name__, error)
                    raise
                else:
                    execution.set(
                        {"ekbatan.action.outcome": "success", "ekbatan.action.retry.count": retried}
                    )
                    LOG.info(
                        "%s completed in %.0fms [principal=%s]",
                        name,
                        (time.monotonic() - started_at) * 1000,
                        principal or "",
                    )
                    return result

    # ---------------------------------------------------------------- committing

    def _commit(
        self,
        action_name: str,
        plan: ActionPlan,
        params: Any,
        started: Any,
        policy: ExecutionConfiguration,
    ) -> None:
        with span("ekbatan.action.persist") as persisting:
            self._flush(action_name, plan, params, started, policy, persisting)

    def _flush(
        self,
        action_name: str,
        plan: ActionPlan,
        params: Any,
        started: Any,
        policy: ExecutionConfiguration,
        persisting: Any,
    ) -> None:
        by_shard = self._group_by_shard(plan)

        if len(by_shard) > 1 and not policy.allow_cross_shard:
            # Refused before anything is written. Committing per shard by default would turn
            # the central promise into a lie the first time an action reached two databases by
            # accident - and it would do it silently.
            first, second, *_ = sorted(by_shard)
            raise CrossShardError(first, second)

        if not by_shard:
            # Nothing was staged, and the invocation is still recorded. See the sentinel
            # row in SingleTableJsonEventPersister.
            by_shard = {self.registry.default_shard: {}}

        if len(by_shard) > 1:
            # Before the loop, not after it: an action that spans shards and then fails partway
            # is exactly when someone needs to know it spanned shards, and a warning written
            # after the last commit is never written at all in that case.
            persisting.set({"ekbatan.shard.cross_shard": True})
            LOG.warning(
                "%s spans %d shards %s; each committed on its own",
                action_name,
                len(by_shard),
                [str(s) for s in by_shard],
            )

        action_id = uuid.uuid4()
        committed: list[ShardIdentifier] = []

        for shard, slices in by_shard.items():
            try:
                self._commit_shard(shard, slices, action_name, action_id, params, started)
                committed.append(shard)
            except BaseException as error:
                # BaseException, and Java's comment on the same line says why: "Caught as
                # Exception, an Error on shard B produced partially-committed data in silence."
                # A KeyboardInterrupt between two commits - a pod going away mid-action - is
                # the realistic one here, and it must not decide whether the alarm is raised.
                if committed:
                    # The documented consequence of opting in, and the only notice anyone gets.
                    persisting.set(
                        {
                            "ekbatan.shard.partial_commit_failure": True,
                            "ekbatan.shard.committed_shards": str([str(x) for x in committed]),
                            "ekbatan.shard.failed_shard": str(shard),
                        }
                    )
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

    def _commit_shard(
        self,
        shard: ShardIdentifier,
        staged: Mapping[type, PersistableChanges],
        action_name: str,
        action_id: uuid.UUID,
        params: Any,
        started: Any,
    ) -> None:
        """One shard, one transaction. What goes in it is the persister's business."""
        with self.registry.manager(shard).transaction() as tx:
            self.changes.persist(
                tx,
                staged,
                namespace=self.namespace,
                action_name=action_name,
                params=params,
                started=started,
                completed=to_instant(self.clock.now()),
                action_id=action_id,
            )

    def _group_by_shard(
        self, plan: ActionPlan
    ) -> dict[ShardIdentifier, dict[type, PersistableChanges]]:
        grouped: dict[ShardIdentifier, dict[type, PersistableChanges]] = {}
        for model_type, changes in plan.changes().items():
            if not changes:
                continue
            repo = self._repository(model_type)
            for staged, adding in ((changes.additions, True), (changes.updates, False)):
                for model in staged.values():
                    # The row, not its id. The repository routes its own writes by the row,
                    # because a strategy may read any field - so asking a different question
                    # here would put the state on one shard and the outbox row on another, in
                    # two transactions, with nothing to say so. It is also the only question a
                    # strategy that does not encode the shard in the id can answer at all.
                    shard = repo.shard_of(model)
                    slice_ = grouped.setdefault(shard, {}).setdefault(
                        model_type, PersistableChanges()
                    )
                    target = slice_.additions if adding else slice_.updates
                    target[model.id] = model
        return grouped

    def _repository(self, model_type: type) -> Repository[Any]:
        return self.changes.repository_for(model_type)


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


class ActionExecutorBuilder:
    """Fluent builder for :class:`ActionExecutor`. Obtain via :meth:`ActionExecutor.builder`.

    Java's setters, less ``objectMapper``: JSON here is the standard library's, and there is no
    mapper object to hand over.
    """

    __slots__ = (
        "_action_registry",
        "_clock",
        "_database_registry",
        "_default_execution_configuration",
        "_event_persister",
        "_namespace",
        "_repository_registry",
    )

    def __init__(self) -> None:
        self._namespace: str | None = None
        self._database_registry: DatabaseRegistry | None = None
        self._repository_registry: RepositoryRegistry | None = None
        self._action_registry: ActionRegistry | None = None
        self._event_persister: EventPersister | None = None
        self._clock: Clock | None = None
        self._default_execution_configuration: ExecutionConfiguration = DEFAULT

    def namespace(self, namespace: str) -> Self:
        self._namespace = namespace
        return self

    def database_registry(self, database_registry: DatabaseRegistry) -> Self:
        self._database_registry = database_registry
        return self

    def repository_registry(self, repository_registry: RepositoryRegistry) -> Self:
        self._repository_registry = repository_registry
        return self

    def action_registry(self, action_registry: ActionRegistry) -> Self:
        self._action_registry = action_registry
        return self

    def event_persister(self, event_persister: EventPersister) -> Self:
        self._event_persister = event_persister
        return self

    def clock(self, clock: Clock) -> Self:
        self._clock = clock
        return self

    def default_execution_configuration(
        self, default_execution_configuration: ExecutionConfiguration
    ) -> Self:
        self._default_execution_configuration = default_execution_configuration
        return self

    def build(self) -> ActionExecutor:
        """The executor. Each missing piece is refused by name, as the constructor refuses it."""
        if self._event_persister is None:
            raise ValidationError("event_persister is required")
        return ActionExecutor(
            namespace=self._namespace,  # type: ignore[arg-type]  # None is refused by name
            registry=self._database_registry,  # type: ignore[arg-type]
            repositories=self._repository_registry,  # type: ignore[arg-type]
            actions=self._action_registry if self._action_registry is not None else (),
            outbox=self._event_persister,
            clock=self._clock,
            config=self._default_execution_configuration,
        )
