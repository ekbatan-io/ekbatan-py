"""What a keyed lock is.

Callers take mutual exclusion on a string key. Different keys are independent; the same key
serialises. Every implementation is reentrant per ``(thread, key)`` and every hold is bounded,
because a holder that crashes, hangs, or forgets to release must not take the key with it.

Two distinctions the interface exists to preserve:

- **Contention is not failure.** A key held elsewhere is ``None`` from :meth:`try_acquire` and
  more waiting in :meth:`acquire`. Only a backend that could not be reached raises. A caller
  cannot tell a broken lock service from a busy one if both arrive the same way, and the two
  call for opposite responses.
- **The outermost holder owns the time limit.** A re-entry's ``max_hold`` is ignored. The
  outer region committed to a worst case and an inner call cannot pull that out from under it -
  stricter than the last-call-wins convention some libraries use, and the only rule under which
  the outer region's reasoning stays true.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from types import TracebackType
from typing import Protocol, runtime_checkable

from ..errors import ValidationError

__all__ = ["KeyedLock", "Lease", "lock_key_hash"]


def lock_key_hash(key: str) -> int:
    """The 64-bit identifier a SQL backend locks on.

    The first eight bytes of the key's SHA-256, read as a signed big-endian integer - the same
    derivation the Java implementation uses, so a Python service and a Java service contending
    for ``wallet:abc`` actually contend rather than each holding their own idea of it.
    """
    digest = hashlib.sha256(key.encode()).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@runtime_checkable
class _Hold(Protocol):
    """What a lease needs from whatever is tracking the hold behind it."""

    @property
    def held(self) -> bool: ...

    def release_one(self) -> None: ...


class Lease:
    """A held lock. Closing releases it; closing twice is harmless.

    Every acquisition returns its own lease, re-entries included, and each must be closed once.
    ``with`` does that; cleanup running from two places - a scope exit and an error path - is
    why closing twice may not raise.
    """

    __slots__ = ("_holding",)

    def __init__(self, holding: _Hold) -> None:
        self._holding = holding

    @property
    def held(self) -> bool:
        """False after release, and false once the hold limit has fired - even for an inner
        lease nobody has closed yet."""
        return self._holding.held

    def release(self) -> None:
        self._holding.release_one()

    def __enter__(self) -> Lease:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def __repr__(self) -> str:
        return f"Lease(held={self.held})"


@runtime_checkable
class KeyedLock(Protocol):
    """Mutual exclusion on a string key."""

    def acquire(self, key: str, max_hold: timedelta) -> Lease:
        """Wait for the key, however long it takes, then hold it for at most ``max_hold``."""
        ...

    def try_acquire(self, key: str, max_wait: timedelta, max_hold: timedelta) -> Lease | None:
        """Wait up to ``max_wait``. ``None`` means someone else has it, not that anything
        went wrong. Pass zero for a single attempt."""
        ...

    def close(self) -> None: ...


def check(key: str, max_hold: timedelta, max_wait: timedelta | None = None) -> None:
    """Refuse the arguments that produce something which looks like a lock and is not."""
    if not key or not key.strip():
        raise ValidationError("lock key cannot be blank; a blank key collides with every other")
    if max_hold <= timedelta(0):
        raise ValidationError(
            f"max_hold must be positive, got {max_hold}; a zero hold has already expired"
        )
    if max_wait is not None and max_wait < timedelta(0):
        raise ValidationError(f"max_wait cannot be negative, got {max_wait}")
