"""`SchedulerName` - who picked a row.

It goes into `picked_by`, which is how an operator answers "which instance is running this"
during an incident, so the default is the hostname rather than something anonymous.
"""

from __future__ import annotations

import logging
import socket
import time
from typing import ClassVar

__all__ = ["Fixed", "Hostname", "SchedulerName"]

LOG = logging.getLogger("db_scheduler.scheduler_name")

#: What goes in `picked_by` when the hostname cannot be resolved. A name, so the column is never
#: empty and the failure is visible rather than silent.
FAILED_HOSTNAME_LOOKUP = "failed.hostname.lookup"

#: A hostname lookup slower than this is worth saying out loud - it usually means DNS or
#: /etc/hosts is misconfigured, and it happens once per process at startup.
SLOW_LOOKUP = 1.0


class SchedulerName:
    """The name this instance picks rows under."""

    __slots__ = ()

    #: Nested in Java as `SchedulerName.Fixed` / `SchedulerName.Hostname`. Bound after both
    #: classes exist; the module-level names are the same objects.
    Fixed: ClassVar[type[SchedulerName]]
    Hostname: ClassVar[type[SchedulerName]]

    @property
    def name(self) -> str:
        raise NotImplementedError


class Fixed(SchedulerName):
    """A name you chose. Useful when the hostname is a container id nobody can act on."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"Fixed({self._name!r})"


class Hostname(SchedulerName):
    """The machine's hostname, resolved once at construction.

    Once, because the lookup can be slow and this is read on every pick.
    """

    __slots__ = ("_cached_hostname",)

    def __init__(self) -> None:
        started = time.monotonic()
        LOG.debug("Resolving hostname..")
        try:
            self._cached_hostname = socket.gethostname()
        except OSError:
            LOG.warning("Failed to resolve hostname. Using dummy-name for scheduler.")
            self._cached_hostname = FAILED_HOSTNAME_LOOKUP
        else:
            LOG.debug("Resolved hostname..")
            duration = time.monotonic() - started
            if duration > SLOW_LOOKUP:
                LOG.warning("Hostname-lookup took %dms", round(duration * 1000))

    @property
    def name(self) -> str:
        return self._cached_hostname

    def __repr__(self) -> str:
        return f"Hostname({self._cached_hostname!r})"


SchedulerName.Fixed = Fixed
SchedulerName.Hostname = Hostname
