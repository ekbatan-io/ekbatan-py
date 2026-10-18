"""Values as Java's log lines print them, so a line reads the same in both ports.

A log search or an alert a Java team wrote against Ekbatan's lines matches only if the text is
the same, values included. These are the few places Python's own printing differs: a shard, a
list, a duration. They are for log messages only; elsewhere a shard prints as ``group:member``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from ._json import _duration

__all__ = ["java_duration", "java_list", "java_shard", "java_shard_pair"]


def java_shard(shard: Any) -> str:
    """Java's ``ShardIdentifier.toString()``: ``ShardIdentifier(1, 0)``."""
    return f"ShardIdentifier({shard.group}, {shard.member})"


def java_shard_pair(shard: Any) -> str:
    """The ``(group,member)`` Java's transaction lines print: ``(1,0)``."""
    return f"({shard.group},{shard.member})"


def java_list(items: Iterable[Any]) -> str:
    """Java's ``List.toString()``: ``[a, b]``, each element as its own text, no quotes."""
    return "[" + ", ".join(str(item) for item in items) + "]"


def java_duration(value: timedelta) -> str:
    """Java's ``Duration.toString()``: ``PT24H``, ``PT1.5S``."""
    return _duration(value)
