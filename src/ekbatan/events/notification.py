"""The queue row, its lifecycle, and how long a retry waits."""

from __future__ import annotations

from datetime import timedelta
from enum import Enum

__all__ = ["NotificationState", "backoff"]


class NotificationState(Enum):
    """Where one (event, handler) pair has got to.

    ``SUCCEEDED`` and ``EXPIRED`` are terminal: those rows leave the dispatch index and are
    never picked up again. The two exist separately because "it worked" and "we gave up" are
    different facts and only one of them is worth paging about.
    """

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


BASE = timedelta(seconds=30)
_MAX_DOUBLINGS = 30


def backoff(attempts: int, cap: timedelta) -> timedelta:
    """How long before the next attempt: doubling from 30 seconds, flattening at ``cap``.

    ``attempts`` counts what has happened so far, so the first failure passes 1 and waits 30
    seconds. The cap matters more than the curve: without one, a handler that fails all
    afternoon schedules its next attempt some time next year.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    if cap <= timedelta(0):
        raise ValueError(f"cap must be positive, got {cap}")
    grown: timedelta = BASE * (2 ** min(attempts - 1, _MAX_DOUBLINGS))
    return min(grown, cap)
