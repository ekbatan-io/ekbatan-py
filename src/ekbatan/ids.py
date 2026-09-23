"""Identifiers that know what they identify - Java's ``Id<Wallet>`` and ``ShardedId<Wallet>``.

A ``UUID`` parameter accepts any UUID, so a wallet id and a notification id are the same type
to a compiler and different things to everyone else. These wrap it so they are different types
too - and, when the id carries shard bits, so that routing can be read off the id without
asking anything else::

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)      # ShardedId[Wallet], not UUID

Spelled as Java spells them, with the model as the type argument::

    ShardedId[Wallet].generate(shard)       # Java: ShardedId.generate(Wallet.class, shard)
    Id[Alert].random()                      # Java: Id.random(Alert.class)
    Id[Alert].of(value)                     # Java: Id.of(Alert.class, value)

The subscript does what Java's class argument does: it tells the checker which model this is,
and nothing else. **Passing one model's id where another's is expected is a type error** - mypy
rejects ``ShardedId[Alert]`` where ``ShardedId[Wallet]`` is wanted, as javac does. At runtime the
model is erased in both languages, measured on Java's own classes: two models' ids with one uuid
are equal, and are one entry in a set. The checker is what holds the line, here as there.

**``ShardedId`` is not an ``Id``**, as it is not in Java: two classes, and an ``Id`` never equals
a ``ShardedId`` even with the same uuid. Raw UUIDs keep working everywhere; this is an option,
not a tax.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime
from functools import total_ordering
from typing import Any, Self

from .errors import ValidationError
from .sharding import (
    DEFAULT_SHARD,
    ShardIdentifier,
    read_shard,
    sharded_uuid,
    uuid_instant,
)

__all__ = ["Id", "ShardedId", "TypedValue", "unwrap"]


@total_ordering
@dataclass(frozen=True, eq=False)
class TypedValue[T]:
    """A primitive with a type of its own.

    Subclass it for anything an application passes around as a bare string or number and
    should not: an account number, an ISIN, a tenant key. The repository unwraps it on the way
    to a column and wraps it on the way back, so the database sees the primitive and your code
    never does.
    """

    value: T

    def __post_init__(self) -> None:
        """There is no such thing as a typed value wrapping nothing.

        Java's constructor says so too. An absent one is ``WalletNumber | None`` at the call
        site, which a checker can see; ``WalletNumber(None)`` is a wrapper that compares equal
        to every other empty wrapper of its type and hashes with them, and reaches the
        database as a null in a column that usually forbids it.
        """
        if self.value is None:
            raise ValidationError(f"{type(self).__name__} value cannot be null")

    def __eq__(self, other: object) -> bool:
        """Equal when the values match and one type is the other or a subclass of it.

        Two *unrelated* wrappers of the same primitive stay unequal, which is the point of
        wrapping them - an ``Id`` and a ``ShardedId`` among them, as Java's ``equals`` keeps them
        apart. A subclass of one of them still equals it, so a value an application wrapped in
        its own subclass compares equal to the same value read back from the database.

        ``Id[Wallet] == Id[Note]`` is true at runtime, because Python erases the parameter just
        as Java does. That distinction is a checker's job, and a checker does make it.
        """
        if not isinstance(other, TypedValue):
            return NotImplemented
        if not (isinstance(other, type(self)) or isinstance(self, type(other))):
            return False
        return bool(self.value == other.value)

    def __hash__(self) -> int:
        return hash(self.value)

    def __lt__(self, other: object) -> bool:
        """Ordered by the value inside.

        Which for a version 7 id means ordered by when it was minted - the property that keeps
        a primary key index from fragmenting the way a random one does.
        """
        if not isinstance(other, TypedValue):
            return NotImplemented
        return bool(self.value < other.value)

    def __str__(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.value!r})"


@dataclass(frozen=True, eq=False)
class Id[M](TypedValue[_uuid.UUID]):
    """A UUID that identifies one kind of thing - Java's ``Id<M>``.

    ``Id[Wallet]`` and ``Id[Notification]`` are different types to a checker and the same
    column to the database.
    """

    value: _uuid.UUID

    @classmethod
    def of(cls, value: _uuid.UUID | str) -> Self:
        if isinstance(value, str):
            try:
                value = _uuid.UUID(value)
            except ValueError:
                raise ValidationError(f"{value!r} is not a uuid") from None
        return cls(value)

    @classmethod
    def random(cls) -> Self:
        """A fresh unsharded id. Version 4 - no ordering, no shard, nothing to decode."""
        return cls(_uuid.uuid4())

    @property
    def shard(self) -> ShardIdentifier | None:
        """The shard this id names, if it carries one.

        ``None`` for an id minted before the deployment was sharded, or by something else
        entirely - and the caller then falls back to the default rather than failing, which is
        what lets one environment read another's ids.
        """
        return read_shard(self.value)

    @property
    def minted_at(self) -> datetime | None:
        """When a version 7 id was created, read out of the id itself."""
        return uuid_instant(self.value)


@dataclass(frozen=True, eq=False)
class ShardedId[M](TypedValue[_uuid.UUID]):
    """An id that says which database its row lives in - Java's ``ShardedId<M>``.

    A version 7 UUID with the shard packed into its rand_b region, so routing needs no lookup
    table and no second column. The bit layout is part of the cross-language contract: a port
    that packs it differently reads another port's rows from the wrong database, and does not
    notice, because the query succeeds and returns nothing.

    **Not an** :class:`Id`, as Java's is not: its own class beside it, never equal to an ``Id``
    of the same uuid. What they share is being a typed uuid, which is :class:`TypedValue`'s job.
    """

    value: _uuid.UUID

    @classmethod
    def of(cls, value: _uuid.UUID | str) -> Self:
        if isinstance(value, str):
            try:
                value = _uuid.UUID(value)
            except ValueError:
                raise ValidationError(f"{value!r} is not a uuid") from None
        return cls(value)

    @classmethod
    def generate(cls, shard: ShardIdentifier) -> Self:
        return cls(sharded_uuid(shard))

    @classmethod
    def random(cls) -> Self:
        """A fresh id on the default shard. Java has no counterpart; it is
        ``generate(DEFAULT_SHARD)``, for code that does not care which shard."""
        return cls.generate(DEFAULT_SHARD)

    @property
    def shard(self) -> ShardIdentifier:
        """The shard, which one of these always has.

        Java reads shard bits out of any uuid it is given, a random version 4 one included,
        and so routes it to a shard nobody chose. This refuses instead.
        """
        found = read_shard(self.value)
        if found is None:
            raise ValidationError(
                f"{self.value} carries no shard - it is a version {self.value.version} uuid. "
                f"Use Id for one that does not, or ShardedId.generate(shard) for one that does."
            )
        return found

    @property
    def minted_at(self) -> datetime | None:
        """When it was created, read out of the id itself."""
        return uuid_instant(self.value)


def unwrap(value: Any) -> Any:
    """The primitive inside a typed value, or the value itself.

    Everywhere the framework has to hand something to the database.
    """
    return value.value if isinstance(value, TypedValue) else value
