"""Identifiers that know what they identify.

A ``UUID`` parameter accepts any UUID, so a wallet id and a notification id are the same type
to a compiler and different things to everyone else. These wrap it so they are different types
too - and, when the id carries shard bits, so that routing can be read off the id without
asking anything else.

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)      # Id[Wallet], not UUID

Passing the wrong one is then a type error rather than an empty result at three in the morning.
Raw UUIDs keep working everywhere; this is an option, not a tax.

**Give each model a named id type.** ``Id[Wallet]`` satisfies a type checker, but Python erases
the parameter at runtime exactly as Java does, so ``Id[Wallet](u) == Id[Note](u)`` is true once
the program is running. A one-line subclass is a real type and is therefore checked twice - by
the checker, and by the interpreter::

    class WalletId(ShardedId["Wallet"]): ...
    class NoteId(Id["Note"]): ...

    WalletId(u) == NoteId(u)        # False, at runtime, with no checker involved
    {WalletId(u), NoteId(u)}        # two entries, not one

The subclass keeps every factory - ``random()``, ``of()``, ``generate(shard)`` - and stays an
``Id``, so nothing else has to know about it. Prefer it; ``Id[Wallet]`` is the shorthand for
when you do not.
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
        """Equal when the values match and the types are in the same family.

        ``ShardedId`` is an ``Id``, and a row read back is rebuilt as whatever its field
        declares - so the one in memory and the one from the database have to compare equal or
        a lookup stops matching what was asked for. Two *unrelated* wrappers of the same
        primitive stay unequal, which is the point of wrapping them.

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
    """A UUID that identifies one kind of thing.

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
class ShardedId[M](Id[M]):
    """An id that says which database its row lives in.

    A version 7 UUID with the shard packed into its rand_b region, so routing needs no lookup
    table and no second column. The bit layout is part of the cross-language contract: a port
    that packs it differently reads another port's rows from the wrong database, and does not
    notice, because the query succeeds and returns nothing.
    """

    @classmethod
    def generate(cls, shard: ShardIdentifier) -> Self:
        return cls(sharded_uuid(shard))

    @classmethod
    def random(cls) -> Self:
        """A fresh id on the default shard.

        Overridden because the inherited version mints a version 4 uuid, which carries no
        shard - and :attr:`shard` would then have to raise on an id this class had just
        produced. A sharded id with no shard named belongs on the default one.
        """
        return cls.generate(DEFAULT_SHARD)

    @property
    def shard(self) -> ShardIdentifier:
        """The shard, which one of these always has."""
        found = read_shard(self.value)
        if found is None:
            raise ValidationError(
                f"{self.value} carries no shard - it is a version {self.value.version} uuid. "
                f"Use Id for one that does not, or ShardedId.generate(shard) for one that does."
            )
        return found


def unwrap(value: Any) -> Any:
    """The primitive inside a typed value, or the value itself.

    Everywhere the framework has to hand something to the database.
    """
    return value.value if isinstance(value, TypedValue) else value
