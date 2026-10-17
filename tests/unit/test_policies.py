"""The framework's policy objects, which decide things without touching anything.

Retry, backoff, schedules, and the plan's own bookkeeping. All pure - a database would tell
you nothing about any of them, and needing one to find out whether a backoff curve flattens
would be a waste of a container.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from ekbatan import RetryConfig, current_plan
from ekbatan.action import ActionPlan
from ekbatan.errors import EkbatanError, ValidationError
from ekbatan.events import backoff
from ekbatan.ids import ShardedId
from fixtures.domain import Wallet


def test_a_policy_cannot_ask_for_negative_time():
    with pytest.raises(ValidationError, match="delay"):
        RetryConfig(delay=-1.0)
    with pytest.raises(ValidationError, match="retries"):
        RetryConfig(max_retries=-1)


def test_staging_the_same_row_twice_in_one_batch_is_refused(clock):

    plan = ActionPlan(clock)
    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=clock.now(),
    )
    with pytest.raises(ValidationError, match="already staged"):
        plan.add_all([wallet, wallet])


def test_a_plan_reports_what_it_is_holding(clock):

    plan = ActionPlan(clock)
    assert not plan

    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=clock.now(),
    )
    plan.add(wallet)

    assert plan
    assert len(plan.additions(Wallet)) == 1
    assert plan.updates(Wallet) == []
    assert list(plan.changes()) == [Wallet]


def test_the_retry_curve_doubles_and_then_flattens():
    cap = timedelta(minutes=5)
    delays = [backoff(n, cap).total_seconds() for n in range(1, 8)]
    assert delays == [30, 60, 120, 240, 300, 300, 300]
    with pytest.raises(ValueError, match="attempts"):
        backoff(0, cap)
    with pytest.raises(ValueError, match="cap"):
        backoff(1, timedelta(0))


def test_a_retry_policy_cannot_ask_for_negative_time():
    with pytest.raises(ValidationError, match="delay"):
        RetryConfig(delay=-1.0)
    with pytest.raises(ValidationError, match="retries"):
        RetryConfig(max_retries=-1)


def test_a_plan_outside_a_unit_of_work_says_so():

    with pytest.raises(EkbatanError, match="no unit of work here"):
        current_plan()


# Schedules are no longer Ekbatan's. A `DistributedJob` returns a db-scheduler `Schedule`, the
# way the Java `DistributedJob` returns db-scheduler's - so `Every`, `FixedDelay` and `Daily`
# left with the hand-rolled scheduler, and their behaviour is tested where they now live
# (`db-scheduler/tests/unit/test_schedules.py`, far more thoroughly than here).


def test_staging_hands_back_the_row_you_passed(clock):
    """Java's `ActionPlanTest.add_returns_the_entity` asserts `isSameAs`. So does this.

    There is nothing left for `add` to fill in: `created_date` is required at construction and
    `updated_date` falls back to it, so the row handed over is already the row that will be
    written. `update` matches Java too - the version has already moved, so the rest of the unit
    of work reads forward.
    """
    from ekbatan.action.plan import ActionPlan
    from fixtures.domain import Wallet

    plan = ActionPlan(clock)
    fresh = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1"),
        created_date=clock.now(),
    )
    assert fresh.created_date == clock.now()
    assert fresh.updated_date == fresh.created_date, "unset, so it falls back to created_date"

    staged = plan.add(fresh)

    assert staged is fresh, "the very object, as Java hands back the very object"
    assert plan.additions(Wallet) == [staged], "and that is what gets written"

    changed = plan.update(
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1"),
            created_date=clock.now(),
        )
    )
    assert changed.version == 2, "Java's `update_returns_next_version`"


def test_staging_nothing_is_not_the_same_as_staging_none(clock):
    """Java's `addAll_with_null_returns_empty` tolerates a null collection and returns empty.

    Here that is a `TypeError`, and deliberately: `None` where an iterable belongs is a bug at
    the call site, and returning empty would let it through to become "the unit of work staged
    nothing" - which commits a sentinel row and looks like success.
    """
    from ekbatan.action.plan import ActionPlan

    plan = ActionPlan(clock)

    assert plan.add_all([]) == []
    assert plan.update_all([]) == []
    assert not plan

    with pytest.raises(TypeError):
        plan.add_all(None)
