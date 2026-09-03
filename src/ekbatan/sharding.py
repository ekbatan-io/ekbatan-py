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
import time
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .errors import ValidationError

__all__ = [
    "EmbeddedBitsSharding",
    "NoSharding",
    "ShardIdentifier",
    "ShardingStrategy",
    "resolve_shard",
    "sharded_uuid",
]

GROUP_BITS = 8
MEMBER_BITS = 6
_SHARD_BITS = GROUP_BITS + MEMBER_BITS
_RANDOM_BITS = 62 - _SHARD_BITS  # 48

MAX_GROUP = (1 << GROUP_BITS) - 1
MAX_MEMBER = (1 << MEMBER_BITS) - 1


@dataclass(frozen=True, slots=True, order=True)
class ShardIdentifier:
    """One database in the layout: 256 groups of up to 64 members."""

    group: int
    member: int

    def __post_init__(self) -> None:
        if not 0 <= self.group <= MAX_GROUP:
            raise ValidationError(f"group must be 0..{MAX_GROUP}, got {self.group}")
        if not 0 <= self.member <= MAX_MEMBER:
            raise ValidationError(f"member must be 0..{MAX_MEMBER}, got {self.member}")

    def __str__(self) -> str:
        return f"{self.group}:{self.member}"

    @property
    def bits(self) -> int:
        return (self.group << MEMBER_BITS) | self.member


DEFAULT_SHARD = ShardIdentifier(0, 0)


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

    def shard_for(self, key: object) -> ShardIdentifier | None:
        """The shard this key names, or ``None`` to use the default."""
        ...


class NoSharding:
    """Everything on one database. The default, and the right answer for most deployments."""

    __slots__ = ()

    def shard_for(self, key: object) -> ShardIdentifier | None:
        return None

    def __repr__(self) -> str:
        return "NoSharding()"


class EmbeddedBitsSharding:
    """Read the shard out of the id's own bits."""

    __slots__ = ()

    def shard_for(self, key: object) -> ShardIdentifier | None:
        if isinstance(key, ShardIdentifier):
            return key
        if isinstance(key, uuid.UUID):
            return read_shard(key)
        if isinstance(key, str):
            try:
                return read_shard(uuid.UUID(key))
            except ValueError:
                return None
        return None

    def __repr__(self) -> str:
        return "EmbeddedBitsSharding()"


def resolve_shard(
    strategy: ShardingStrategy, key: object, default: ShardIdentifier
) -> ShardIdentifier:
    return strategy.shard_for(key) or default
