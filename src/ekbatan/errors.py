"""Every failure the framework raises, and what each one means.

The classes mirror `errorKind` in ekbatan-spec: a caller has to be able to tell a bad request
from a lost race from an unreachable database, because the three call for opposite responses.
Ports name their own types; the spec only ever classifies.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ConnectivityError",
    "CrossShardError",
    "EkbatanError",
    "EntityNotFoundError",
    "LockAcquisitionError",
    "StaleRecordError",
    "UnsupportedError",
    "ValidationError",
]


class EkbatanError(Exception):
    """Base for everything this framework raises on purpose."""


class ValidationError(EkbatanError, ValueError):
    """The input was rejected before anything was attempted.

    Also a ``ValueError`` so that ordinary Python code catching ``ValueError`` around a
    domain call keeps working - a framework that makes people learn a new exception
    hierarchy for "you passed a bad number" has charged them for nothing.
    """


class EntityNotFoundError(EkbatanError, LookupError):
    """A row a caller required is not there."""

    def __init__(self, model: str, key: Any) -> None:
        super().__init__(f"no {model} with id {key!r}")
        self.model = model
        self.key = key


class StaleRecordError(EkbatanError):
    """The row moved under us: its version is no longer the one we read.

    Retryable, and retried once by default. Distinct from a validation error because the
    caller did nothing wrong and the right response is to try again, not to fix the input.
    """

    def __init__(
        self, message: str, *, expected: int | None = None, model: str | None = None
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.model = model


class CrossShardError(EkbatanError):
    """One unit of work touched more than one shard without permission.

    There is no two-phase commit here and the framework does not pretend otherwise. Committing
    per shard by default would turn the central promise - all or nothing - into a lie the first
    time an action reached two databases by accident.
    """

    def __init__(self, shards: object) -> None:
        super().__init__(
            f"this unit of work spans shards {shards}; there is no cross-shard transaction. "
            f"Pass allow_cross_shard=True to commit each shard independently, understanding "
            f"that a later shard failing leaves earlier ones committed."
        )
        self.shards = shards


class LockAcquisitionError(EkbatanError):
    """A keyed lock could not be obtained because its backend failed.

    Never raised for contention: a lock held elsewhere is an empty result, not an error. A
    caller cannot tell a broken backend from a busy one if both arrive the same way, and the
    two call for opposite responses.
    """

    def __init__(self, key: str, message: str) -> None:
        super().__init__(f"lock {key!r}: {message}")
        self.key = key


class ConnectivityError(EkbatanError):
    """The database could not be reached."""


class UnsupportedError(EkbatanError):
    """The operation is not available on this dialect."""
