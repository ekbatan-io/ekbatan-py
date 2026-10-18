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
import sys
import threading
from datetime import timedelta
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

from ..errors import ValidationError

__all__ = ["KeyedLockProvider", "Lease", "lock_key_hash"]


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


class _LeaseCore:
    """What a sync and an async lease share: the hold behind it, and closing it only once.

    Each lease remembers that it was closed, as Java's ``HeldLease`` does. Without that, closing
    an inner lease twice counted the hold down twice: the second close freed the key while the
    outer region still held it, and another holder walked in.
    """

    __slots__ = ("_closed", "_gate", "_holding")

    def __init__(self, holding: Any) -> None:
        self._holding = holding
        self._gate = threading.Lock()
        self._closed = False

    @property
    def held(self) -> bool:
        """False once this lease is closed, and false once the hold limit has fired - even for
        an inner lease nobody has closed yet."""
        return not self._closed and bool(self._holding.held)

    def _close_once(self) -> bool:
        """Mark it closed; True only for the first caller. The check and the set together, as
        Java's compareAndSet: two closes at once must not both count the hold down."""
        with self._gate:
            if self._closed:
                return False
            self._closed = True
            return True

    def __repr__(self) -> str:
        return f"{type(self).__name__}(held={self.held})"


class Lease(_LeaseCore):
    """A held lock. Closing releases it; closing twice is harmless.

    Every acquisition returns its own lease, re-entries included, and each must be closed once.
    ``with`` does that; cleanup running from two places - a scope exit and an error path - is
    why closing twice may not raise.
    """

    __slots__ = ()

    def __init__(self, holding: _Hold) -> None:
        super().__init__(holding)

    def release(self) -> None:
        if self._close_once():
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


@runtime_checkable
class KeyedLockProvider(Protocol):
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
    """Refuse the arguments that produce something which looks like a lock and is not - and a
    sync lock taken on an event loop's thread."""
    _refuse_on_an_event_loop()
    check_arguments(key, max_hold, max_wait)


def check_arguments(key: str, max_hold: timedelta, max_wait: timedelta | None = None) -> None:
    """The argument checks alone, shared with ``ekbatan.aio.locks``."""
    if not key or not key.strip():
        raise ValidationError("lock key cannot be blank; a blank key collides with every other")
    if max_hold <= timedelta(0):
        raise ValidationError(
            f"max_hold must be positive, got {max_hold}; a zero hold has already expired"
        )
    if max_wait is not None and max_wait < timedelta(0):
        raise ValidationError(f"max_wait cannot be negative, got {max_wait}")


def _refuse_on_an_event_loop() -> None:
    """Refuse a lock taken on an asyncio event loop's own thread.

    A holder is a thread, as in Java, where each request has a thread of its own. On an event
    loop every request shares one: a second request would "re-enter" the first one's lock and
    walk straight in, and a request that did wait would freeze the loop - the holder included.
    On an event loop the answer is the async providers in ``ekbatan.aio.locks``, whose holder is
    the task; from sync code, a worker thread, where each request is its own holder again: a
    plain ``def`` endpoint, which FastAPI and Starlette already run on one.

    asyncio not imported means no loop can be running, so then this costs a dictionary lookup.
    """
    asyncio = sys.modules.get("asyncio")
    if asyncio is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise ValidationError(
        "a sync keyed lock was taken on an asyncio event loop's thread: there, two requests "
        "would share one lock holder and both get in. On an event loop use the async provider "
        "from ekbatan.aio.locks, whose holder is the task; or call this from a worker thread - "
        "a plain `def` endpoint, or `await asyncio.to_thread(...)`"
    )
