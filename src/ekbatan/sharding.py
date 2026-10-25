"""Which database a row lives in, and how the row's own id says so.

Routing needs no lookup table and no extra column: the shard is encoded in the identifier. A
caller holds an id and the framework works out the database, which is what keeps a deployment
concern out of business logic.

The bit layout is part of the cross-language contract. A port that packs the shard field at a
different offset will read another port's rows from the wrong database - and will not notice,
because the query succeeds and simply returns nothing.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol, runtime_checkable

from .errors import ValidationError
from .time import Instant

#: The epoch, no zone attached.
_EPOCH = datetime(1970, 1, 1)

__all__ = [
    "DEFAULT_SHARD",
    "MAX_GROUP",
    "MAX_MEMBER",
    "MIN_GROUP",
    "MIN_MEMBER",
    "EmbeddedBitsShardingStrategy",
    "NoShardingStrategy",
    "ShardIdentifier",
    "ShardingStrategy",
    "UnknownShardPolicy",
    "read_shard",
    "resolve_shard",
    "sharded_uuid",
    "uuid_instant",
]

GROUP_BITS = 8
MEMBER_BITS = 6
_SHARD_BITS = GROUP_BITS + MEMBER_BITS
_RANDOM_BITS = 62 - _SHARD_BITS  # 48

#: The coordinate range, which is the range the bits in a sharded uuid can hold. Named as Java
#: names them, because `ShardIdentifierTest` builds its bounds cases out of the four constants
#: and a port that only exports two makes the same test unwritable.
MIN_GROUP = 0
MAX_GROUP = (1 << GROUP_BITS) - 1
MIN_MEMBER = 0
MAX_MEMBER = (1 << MEMBER_BITS) - 1


@dataclass(frozen=True, slots=True, order=True)
class ShardIdentifier:
    """One database in the layout: 256 groups of up to 64 members."""

    group: int
    member: int

    def __post_init__(self) -> None:
        if not MIN_GROUP <= self.group <= MAX_GROUP:
            raise ValidationError(f"group must be {MIN_GROUP}..{MAX_GROUP}, got {self.group}")
        if not MIN_MEMBER <= self.member <= MAX_MEMBER:
            raise ValidationError(f"member must be {MIN_MEMBER}..{MAX_MEMBER}, got {self.member}")

    @classmethod
    def of(cls, group: int, member: int) -> ShardIdentifier:
        return cls(group, member)

    def __str__(self) -> str:
        return f"{self.group}:{self.member}"

    @property
    def bits(self) -> int:
        return (self.group << MEMBER_BITS) | self.member


#: Where a single-database deployment lives, and the fallback for an id naming a shard this
#: deployment has not configured.
DEFAULT_SHARD = ShardIdentifier(0, 0)
ShardIdentifier.DEFAULT = DEFAULT_SHARD  # type: ignore[attr-defined]


def sharded_uuid(shard: ShardIdentifier, *, at_ms: int | None = None) -> uuid.UUID:
    """Mint a UUIDv7 carrying ``shard`` in its rand_b region.

    Version 7 rather than 4 so ids sort by creation time, which is what makes them usable as
    primary keys without wrecking index locality::

        MSB: [48-bit millisecond timestamp][4-bit version = 0111][12-bit random]
        LSB: [2-bit variant = 10][8-bit group][6-bit member][48-bit random]
    """
    timestamp = int(time.time() * 1000) if at_ms is None else at_ms
    noise = int.from_bytes(os.urandom(10), "big")

    msb = ((timestamp & 0xFFFF_FFFF_FFFF) << 16) | 0x7000 | (noise & 0xFFF)
    lsb = (0b10 << 62) | (shard.bits << _RANDOM_BITS) | ((noise >> 12) & ((1 << _RANDOM_BITS) - 1))

    return uuid.UUID(int=(msb << 64) | lsb)


def uuid_instant(value: uuid.UUID) -> Instant | None:
    """When a version 7 id was minted, read out of the id itself.

    Version 7 puts a millisecond timestamp in the high bits, which is what makes these ids sort
    by creation time. Reading it back gives you a free created-at for anything holding an id -
    useful for a range scan on the primary key, or for spotting a row far older than its
    neighbours without joining anything.

    UTC with no zone attached, as :class:`~ekbatan.clock.SystemClock` gives it, so the two
    compare.
    """
    if value.version != 7:
        return None
    return Instant(_EPOCH + timedelta(milliseconds=value.int >> 80))


def read_shard(value: uuid.UUID) -> ShardIdentifier | None:
    """Read the shard back out, or ``None`` if this id does not carry one.

    Never raises. An id minted before the deployment was sharded, or by something else
    entirely, simply has no shard and the caller falls back to the default - which is what lets
    one environment read another's ids instead of rejecting them.
    """
    if value.version != 7:
        return None
    bits = (value.int >> _RANDOM_BITS) & ((1 << _SHARD_BITS) - 1)
    return ShardIdentifier(group=bits >> MEMBER_BITS, member=bits & MAX_MEMBER)


@runtime_checkable
class ShardingStrategy(Protocol):
    """How a repository decides where one row belongs."""

    @property
    def uses_shard_aware_id(self) -> bool:
        """Whether an id alone is enough to say where its row lives.

        A strategy routing on something other than the id - a tenant column, a lookup table -
        cannot answer ``find(id)`` without reading first, and says so here rather than
        guessing. Its repository has to override the id-based resolution.
        """
        ...

    def shard_for_id(self, key: object) -> ShardIdentifier | None:
        """The shard this id names, or ``None`` to use the default."""
        ...

    def shard_for(self, persistable: object) -> ShardIdentifier | None:
        """The shard this row belongs on. Usually its id, but a strategy may read any field."""
        ...


class NoShardingStrategy:
    """Everything on one database. The default, and the right answer for most deployments.

    ``uses_shard_aware_id`` is ``False``, as it is in Java, and it means what it says: there is
    no shard encoded in an id here, because there are no shards. It does *not* mean that reads
    by id are refused - a single-database deployment answers ``find(id)`` perfectly well, and
    the repository special-cases this strategy for exactly that. Java's guard is the same two
    lines, an ``instanceof NoShardingStrategy`` before the flag is consulted.
    """

    __slots__ = ()

    uses_shard_aware_id = False

    def shard_for_id(self, key: object) -> ShardIdentifier | None:
        return None

    def shard_for(self, persistable: object) -> ShardIdentifier | None:
        return None

    def __repr__(self) -> str:
        return "NoShardingStrategy()"


class EmbeddedBitsShardingStrategy:
    """Read the shard out of the id's own bits.

    No lookup table and no extra column, which is what lets a caller holding an id get routing
    for free. An id that carries no shard - one minted before the deployment was sharded, or by
    something else entirely - resolves to nothing and the caller falls back to the default.
    """

    __slots__ = ()

    uses_shard_aware_id = True

    def shard_for_id(self, key: object) -> ShardIdentifier | None:
        if isinstance(key, ShardIdentifier):
            return key
        key = getattr(key, "value", key)  # an Id or any other typed wrapper
        if isinstance(key, uuid.UUID):
            return read_shard(key)
        if isinstance(key, str):
            try:
                return read_shard(uuid.UUID(key))
            except ValueError:
                return None
        return None

    def shard_for(self, persistable: object) -> ShardIdentifier | None:
        return self.shard_for_id(getattr(persistable, "id", persistable))

    def __repr__(self) -> str:
        return "EmbeddedBitsShardingStrategy()"


def resolve_shard(
    strategy: ShardingStrategy, key: object, default: ShardIdentifier
) -> ShardIdentifier:
    return strategy.shard_for_id(key) or default


class UnknownShardPolicy(Enum):
    """What to do with a shard this deployment has not configured - Java's ``UnknownShardPolicy``.

    The fallback exists because shard layouts differ between environments and grow over time: a
    staging database holding one shard has to be able to read ids minted where there are four.
    It is also exactly what a mistyped shard identifier looks like, and that one writes to the
    wrong database; nothing at the call site can tell the two apart. So the fallback stays the
    default and is logged every time it fires, and a deployment that holds the whole layout -
    where an unregistered shard can only be a bug - can ask for :attr:`FAIL` instead.
    """

    #: Route it to the default shard, and log a warning saying so. The historical behaviour.
    USE_DEFAULT_SHARD = "USE_DEFAULT_SHARD"
    #: Raise. For a deployment that holds every shard in the layout.
    FAIL = "FAIL"

    @classmethod
    def from_(cls, value: str | None) -> UnknownShardPolicy:
        """Read a configured value as Java's ``UnknownShardPolicy.from`` does - the trailing
        underscore only because ``from`` is a Python keyword.

        Case and separators do not matter - everything but letters is dropped - so ``fail``,
        ``FAIL``, ``useDefaultShard``, ``use_default_shard`` and ``USE-DEFAULT-SHARD`` all read.
        ``None``, empty or blank is :attr:`USE_DEFAULT_SHARD`, as Java's null or blank is.
        """
        if value is None or not value.strip():
            return cls.USE_DEFAULT_SHARD
        normalised = re.sub(r"[^a-z]", "", value.strip().lower())
        if normalised == "usedefaultshard":
            return cls.USE_DEFAULT_SHARD
        if normalised == "fail":
            return cls.FAIL
        raise ValidationError(
            f"unknown_shard_policy must be 'useDefaultShard' or 'fail', not {value!r}"
        )
