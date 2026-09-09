"""The checks Java centralises in `Validate`, tested where this port actually keeps them.

Java's `ValidateTest` exercises one internal helper - `notNull`, `notBlank`, `isTrue`,
`notEmpty`, `inclusiveBetween`, `isInstanceOf` - and every caller then inherits its semantics
for free. There is no such helper here: Python raises `ValidationError` inline at each site, and
`errors.py` carries only the hierarchy. That is the divergence the ledger records, and it has a
cost this file is the answer to - **eight separate blank-checks can drift apart, where one
`notBlank` cannot**.

So the cases below are Java's contract applied at every site that implements it, rather than one
test of a helper that does not exist. Two rules carry across:

- **Blank means whitespace-only, not just empty.** `"  "` must be refused exactly as `""` is.
  Java gets this from `notBlank`; here each site spells it `not x or not x.strip()`, and a site
  that ever spells it `if not x` would pass an empty-string test and still let `"  "` through.
- **A range is inclusive at both endpoints.**

What does *not* carry across is Java's split between `NullPointerException` for null and
`IllegalArgumentException` for empty. Everything here is a `ValidationError`, which is also a
`ValueError` - one kind of mistake, one kind of refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest

from ekbatan.action.executor import _valid_namespace
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import job_registry
from ekbatan.domain import ModelEvent
from ekbatan.errors import EkbatanError, LockAcquisitionError, ValidationError
from ekbatan.events.handler import EventHandlerRegistry
from ekbatan.events.jobs import _valid_name
from ekbatan.locks.base import check as check_lock_args
from ekbatan.locks.redis import RedisKeyedLockProvider
from ekbatan.sharding import MAX_GROUP, MAX_MEMBER, MIN_GROUP, MIN_MEMBER, ShardIdentifier

BLANK = ["", " ", "   \t\n"]


@dataclass(frozen=True, kw_only=True)
class SomethingHappened(ModelEvent):
    pass


class Idless:
    """Stands in for a model whose id never got set."""

    def __init__(self, id: object) -> None:
        self.id = id


class FakeRedis:
    def register_script(self, _script: str) -> object:
        return object()


class BlankNamed:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def event_type(self) -> type:
        return SomethingHappened

    def handle(self, envelope: object) -> None: ...


# -- notBlank: whitespace is blank, at every site that says so ---------------------------------


@pytest.mark.parametrize("blank", BLANK)
class TestEveryBlankCheckAgrees:
    """One case per inlined `notBlank`. Java needs none of these; it has one helper."""

    def test_a_datasource_url(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="url is required"):
            DataSourceConfig(url=blank)

    def test_an_action_namespace(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="namespace is required"):
            _valid_namespace(blank)

    def test_a_lock_key(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="lock key cannot be blank"):
            check_lock_args(blank, timedelta(seconds=1))

    def test_a_redis_lock_namespace(self, blank: str) -> None:
        with pytest.raises(LockAcquisitionError, match="namespace cannot be blank"):
            RedisKeyedLockProvider(FakeRedis(), namespace=blank)

    def test_an_event_handler_name(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="blank name"):
            EventHandlerRegistry().add(BlankNamed(blank))

    def test_an_event_job_name(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="blank name"):
            _valid_name(blank, "EventFanoutJob")

    def test_a_distributed_job_name(self, blank: str) -> None:
        from db_scheduler.task.schedule import FixedDelay

        from ekbatan.distributedjobs import DistributedJob

        class Blank(DistributedJob):
            def name(self) -> str:
                return blank

            def schedule(self) -> object:
                return FixedDelay(timedelta(seconds=10))

            def execute(self, ctx: object) -> None: ...

        with pytest.raises(ValidationError, match="blank name"):
            job_registry().engine(object()).with_job(Blank()).build()  # type: ignore[arg-type]

    def test_a_model_id_an_event_is_attached_to(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="no id"):
            SomethingHappened().about(Idless(blank))


def test_a_blank_check_that_only_tested_empty_would_pass_the_empty_case_alone() -> None:
    """Why the parametrisation above is worth its length.

    `if not x` and `if not x or not x.strip()` agree on `""` and disagree on `"  "`. A site
    written the first way passes an empty-string test and still admits a whitespace name, which
    is how a job registers under a primary key of three spaces.
    """
    assert (not "") == (not "".strip()) is True
    assert (not "  ") is False
    assert (not "  ".strip()) is True


# -- inclusiveBetween: both endpoints are inside -----------------------------------------------


class TestInclusiveBetween:
    def test_returns_normally_at_endpoints_and_inside(self) -> None:
        assert ShardIdentifier(MIN_GROUP, MIN_MEMBER).bits == 0
        assert ShardIdentifier(MAX_GROUP, MAX_MEMBER).group == MAX_GROUP
        assert ShardIdentifier(5, 5).member == 5

    @pytest.mark.parametrize(
        ("group", "member"),
        [(MIN_GROUP - 1, 0), (MAX_GROUP + 1, 0), (0, MIN_MEMBER - 1), (0, MAX_MEMBER + 1)],
    )
    def test_throws_outside_the_range(self, group: int, member: int) -> None:
        with pytest.raises(ValidationError):
            ShardIdentifier(group, member)


# -- notEmpty, and the one hierarchy rule that replaces Java's two exception types -------------


def test_not_empty_refuses_a_registry_with_no_jobs() -> None:
    with pytest.raises(ValidationError, match="at least one DistributedJob"):
        job_registry().engine(object()).build()  # type: ignore[arg-type]


def test_every_refusal_is_one_kind_of_error() -> None:
    """Java distinguishes null (`NullPointerException`) from empty (`IllegalArgumentException`).
    Here both are a `ValidationError`, which is an `EkbatanError` so framework code can catch
    the family, and a `ValueError` so ordinary Python code catching `ValueError` around a domain
    call keeps working."""
    with pytest.raises(ValidationError) as raised:
        DataSourceConfig(url="")

    assert isinstance(raised.value, EkbatanError)
    assert isinstance(raised.value, ValueError)
