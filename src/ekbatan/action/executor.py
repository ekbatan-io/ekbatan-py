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
from typing import Any

from sqlalchemy import Table

from .._tracing import span
from ..clock import Clock, SystemClock, to_storage
from ..errors import CrossShardError, StaleRecordError, ValidationError
from ..persistence import DatabaseRegistry
from ..repository import Repository
from ..sharding import ShardIdentifier
from .action import Action, _Binding
from .outbox import EventPersister, SingleTableJsonEventPersister
from .persister import ChangePersister
from .plan import ActionPlan, PersistableChanges

__all__ = ["ActionExecutor", "ExecutionConfiguration", "RetryConfig"]

LOG = logging.getLogger("ekbatan.action")

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
    #: How many times to try *again*, so one run plus this many. Named for what it counts:
    #: `attempts=1` reading as one attempt and running twice is the sort of thing nobody
    #: catches in review and everybody hits in production.
    retries: int = 1
    delay: float = 0.1

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValidationError(f"retries cannot be negative, got {self.retries}")
        if self.delay < 0:
            # Caught here rather than at the first retry, which is a different request, on a
            # different day, inside a sleep with nothing pointing back at this line.
            raise ValidationError(f"delay cannot be negative, got {self.delay}")

    def matches(self, error: BaseException) -> bool:
        return isinstance(error, self.on)


#: One quick retry on a lost version race, and nothing else. Enough to absorb ordinary
#: contention; not enough to hide a real problem.
DEFAULT_RETRIES: tuple[RetryConfig, ...] = (RetryConfig(),)


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecutionConfiguration:
    """Per-call policy."""

    #: Tried in order; the first whose `on` matches wins. Order is the only precedence rule,
    #: so a narrow policy goes before a broad one.
    retries: tuple[RetryConfig, ...] = DEFAULT_RETRIES
    allow_cross_shard: bool = False

    @classmethod
    def no_retry(cls, **kwargs: Any) -> ExecutionConfiguration:
        """A failure reaches the caller unchanged.

        For a unit of work whose side effects are not safe to repeat - and the caller is the
        only one who knows that.
        """
        return cls(retries=(), **kwargs)

    def policy_for(self, error: BaseException) -> tuple[RetryConfig, BaseException] | None:
        """The policy for this failure, looking through what it was raised from.

        A driver wrapping a conflict, or a handler re-raising one, would otherwise be
        unrecognisable - the retryable failure is there, one link down. Python's own traceback
        walks the chain the same way: an explicit ``raise … from …`` first, then the error that
        was being handled, unless that was deliberately suppressed.
        """
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            for policy in self.retries:
                if policy.matches(current):
                    return policy, current
            following = current.__cause__
            if following is None and not current.__suppress_context__:
                following = current.__context__
            current = following
        return None


DEFAULT = ExecutionConfiguration()


class ActionExecutor:
    """Runs units of work.

    Actions are passed as instances, not looked up by name. An action is an object you built
    with the things it needs; a registry between the caller and it would buy indirection and
    one more place for a name to be wrong.
    """

    __slots__ = ("changes", "clock", "config", "namespace", "outbox", "registry")

    def __init__(
        self,
        *,
        namespace: str,
        registry: DatabaseRegistry,
        repositories: Iterable[Repository[Any, Any]],
        events: Table | None = None,
        outbox: EventPersister | None = None,
        clock: Clock | None = None,
        config: ExecutionConfiguration = DEFAULT,
    ) -> None:
        self.namespace = _valid_namespace(namespace)
        self.registry = registry
        self.clock = clock or SystemClock()
        self.config = config

        if outbox is not None:
            self.outbox: EventPersister = outbox
        elif events is not None:
            self.outbox = SingleTableJsonEventPersister(events)
        else:
            raise ValidationError("pass either events=<Table> or outbox=<EventPersister>")

        self.changes = ChangePersister(repositories, self.outbox)

    # ------------------------------------------------------------------ running

    def execute[P, R](
        self,
        action: Action[P, R],
        params: P,
        *,
        principal: str | None = None,
        config: ExecutionConfiguration | None = None,
    ) -> R:
        """Run one unit of work and commit what it staged."""
        policy = config or self.config
        name = action.name
        retried = 0

        with span(
            "ekbatan.action.execute",
            {"ekbatan.action.name": name, "ekbatan.action.principal": principal or ""},
        ) as execution:
            started_at = time.monotonic()
            LOG.info("executing %s [principal=%s]", name, principal or "")
            while True:
                started = to_storage(self.clock.now())
                # A fresh plan per attempt. If it survived a failed one, the retry would commit
                # the first attempt's staged changes on top of its own - two deposits for one
                # call.
                plan = ActionPlan(self.clock)
                try:
                    with span("ekbatan.action.perform"), _Binding(plan, principal, self.clock):
                        result = action.perform(params)
                    self._commit(name, plan, params, started, policy)
                except BaseException as error:
                    # BaseException, not Exception. Something that is not an ordinary error -
                    # a cancellation, a failure the runtime raises - still failed this unit of
                    # work, and catching only Exception meant it was recorded as neither a
                    # success nor a failure: no log line, no span status, nothing.
                    match = policy.policy_for(error) if isinstance(error, Exception) else None
                    if match is not None and retried < match[0].retries:
                        retried += 1
                        chosen, cause = match
                        LOG.warning(
                            "retrying %s (%d/%d) after %s: %s",
                            name,
                            retried,
                            chosen.retries,
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
            raise CrossShardError(sorted(str(s) for s in by_shard))

        if not by_shard:
            # Nothing was staged, and the invocation is still recorded. See the sentinel
            # row in SingleTableJsonEventPersister.
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

        if len(by_shard) > 1:
            persisting.set({"ekbatan.shard.cross_shard": True})
            LOG.warning(
                "%s spans %d shards %s; each committed on its own",
                action_name,
                len(by_shard),
                [str(s) for s in by_shard],
            )

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
        with self.registry.manager(shard).transaction() as conn:
            self.changes.persist(
                conn,
                staged,
                namespace=self.namespace,
                action_name=action_name,
                params=params,
                started=started,
                completed=to_storage(self.clock.now()),
                shard=shard,
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

    def _repository(self, model_type: type) -> Repository[Any, Any]:
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
