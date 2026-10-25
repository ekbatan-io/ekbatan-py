"""Dracula 47: a repository's key is the id column's own type, as Java's ``DB_ID`` is - the user's
decision, 2026-10-25, and their correction of the same day.

A plain uuid id column holds a ``UUID``, so the reads by id take ``wallet.id.value``, as Java
writes ``getById(wallet.id.getValue())``, and a typed id there is refused, saying to pass its
``.value``. A column a forced type converts to a typed id holds that typed id - as a jOOQ forced
type changes Java's ``DB_ID`` - so there the typed id is the key: it reaches the column as it is,
for the converter to convert, and a raw ``UUID`` is refused. Either refusal names the type the
column holds. Code that holds a model unwraps the model's own id itself, so writes are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import BigInteger, Column, MetaData, String, Table, func, select

from conftest import GLOBAL
from ekbatan.domain import Entity, GenericState
from ekbatan.ids import ShardedId
from ekbatan.repository import EntityRepository
from ekbatan.schema import converted, uuid_column
from fixtures.actions import WalletCreateAction
from fixtures.executing import run
from modes import rebased

MODES = ("sync", "async")

#: Every read that takes an id, called with one.
READS = {
    "find_by_id": lambda repository, key: repository.find_by_id(key),
    "get_by_id": lambda repository, key: repository.get_by_id(key),
    "exists_by_id": lambda repository, key: repository.exists_by_id(key),
    "find_all_by_ids": lambda repository, key: repository.find_all_by_ids([key]),
    "id_matches": lambda repository, key: repository.id_matches(key),
    "shard_for": lambda repository, key: repository.shard_for(key),
}


# ------------------------------------------------------------------ a plain uuid id column


@pytest.fixture
def wallet(executor):
    return run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")
        ),
    )


@pytest.mark.parametrize("method", sorted(READS))
def test_a_plain_id_column_refuses_a_typed_id_and_says_to_pass_its_value(
    wallet_repository, wallet, method
):
    with pytest.raises(
        TypeError,
        match=rf"\.{method}\(\) takes the id column's own type - wallets\.id holds UUID - "
        rf"not a ShardedId; pass its \.value",
    ):
        READS[method](wallet_repository, wallet.id)


def test_a_plain_id_column_reads_by_the_raw_id(wallet_repository, wallet):
    raw = wallet.id.value
    assert isinstance(raw, UUID)

    assert wallet_repository.get_by_id(raw).id == wallet.id
    assert wallet_repository.find_by_id(raw).id == wallet.id
    assert wallet_repository.exists_by_id(raw)
    assert [found.id for found in wallet_repository.find_all_by_ids([raw, raw])] == [wallet.id]
    assert wallet_repository.find_by_id(uuid4()) is None


# ------------------------------------------------- an id column a forced type converts


@dataclass(frozen=True, kw_only=True)
class Badge(Entity[ShardedId["Badge"]]):
    label: str

    def validate(self) -> None:
        pass


class BadgeIdConverter:
    """What a schemagen forced type on ``badges.id`` builds: the column holds the typed id."""

    def from_db(self, value: UUID) -> ShardedId[Badge]:
        return ShardedId[Badge](value)

    def to_db(self, value: ShardedId[Badge]) -> UUID:
        return value.value


BADGES = Table(
    "badges_by_typed_id",
    MetaData(),
    Column("id", converted(uuid_column(), BadgeIdConverter()), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("label", String(64), nullable=False),
)


@rebased
class BadgeRepository(EntityRepository[Badge, dict[str, Any], ShardedId[Badge]]):
    def to_record(self, badge: Badge) -> dict[str, Any]:
        return {
            "id": badge.id,
            "version": badge.version,
            "state": badge.state.name,
            "label": badge.label,
        }

    def from_record(self, record: dict[str, Any]) -> Badge:
        return Badge(
            id=record["id"],
            version=record["version"],
            state=GenericState[record["state"]],
            label=record["label"],
        )


@pytest.fixture
def badge_repository(backend, registry, clock):
    engine = backend.engine("ekbatan_main")
    try:
        BADGES.metadata.drop_all(engine)
        BADGES.metadata.create_all(engine)
    finally:
        engine.dispose()
    yield BadgeRepository(Badge, BADGES, registry, clock=clock)
    engine = backend.engine("ekbatan_main")
    try:
        BADGES.metadata.drop_all(engine)
    finally:
        engine.dispose()


def a_badge(label: str = "first") -> Badge:
    return Badge(
        id=ShardedId[Badge].generate(GLOBAL), version=1, state=GenericState.ACTIVE, label=label
    )


def test_a_converted_id_column_takes_its_typed_id_and_round_trips_it(badge_repository):
    badge, other = badge_repository.add(a_badge()), badge_repository.add(a_badge("second"))

    found = badge_repository.get_by_id(badge.id)
    assert isinstance(found.id, ShardedId)
    assert found.id == badge.id and found.label == "first"
    assert badge_repository.find_by_id(badge.id).id == badge.id
    assert badge_repository.exists_by_id(badge.id)
    assert {b.id for b in badge_repository.find_all_by_ids([badge.id, other.id, badge.id])} == {
        badge.id,
        other.id,
    }
    assert badge_repository.find_by_id(ShardedId[Badge].generate(GLOBAL)) is None
    assert badge_repository.shard_for(badge.id) == GLOBAL
    with badge_repository.readonly_db() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(BADGES).where(badge_repository.id_matches(badge.id))
            ).scalar_one()
            == 1
        )

    updated = badge_repository.update(replace(found, label="renamed"))
    assert badge_repository.get_by_id(badge.id).label == "renamed"
    assert updated.version == 2


@pytest.mark.parametrize("method", sorted(READS))
def test_a_converted_id_column_refuses_a_raw_uuid_and_names_its_typed_id(
    badge_repository, method
):
    badge = badge_repository.add(a_badge())
    with pytest.raises(
        TypeError,
        match=rf"\.{method}\(\) takes the id column's own type - badges_by_typed_id\.id holds "
        rf"ShardedId - not a UUID; pass the ShardedId the column is converted to",
    ):
        READS[method](badge_repository, badge.id.value)
