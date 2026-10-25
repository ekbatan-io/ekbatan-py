"""The SIGTERM half of `register_shutdown_hook`: one handler for the whole process.

Java's `JobRegistry` turns on db-scheduler's JVM shutdown hook by default, and the JVM runs its
shutdown hooks however the process ends: by itself, or on SIGTERM - what `docker stop` and
Kubernetes send. CPython has a separate mechanism for each. `atexit` covers the first, and
`JobRegistry.start()` puts each registry on it. This module covers the second.

CPython has one handler per signal, set from the main thread only. So the handler is taken only
where nothing else handles SIGTERM - a plain worker, where the signal would stop the process on
the spot. uvicorn, gunicorn, asyncio's `add_signal_handler` and an application's own handler are
left alone: they own shutdown then, and the application's shutdown code calls
`JobRegistry.stop()`.

On SIGTERM the handler stops every registry that asked for the hook - so their running jobs
finish, each bounded by `shutdown_max_wait` - and then the process dies of the signal, as it
would have, only later. It does not turn SIGTERM into a normal exit, as Ctrl-C is turned: that
would also run `finally` blocks and `atexit`, but would first wait for every non-daemon thread
the application started, and one that never ends would keep the process alive until it is killed.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from types import FrameType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import JobRegistry

LOG = logging.getLogger("io.ekbatan.distributedjobs.JobRegistry")

#: The started registries that asked for the hook and have not stopped, in the order they started.
_registries: list[JobRegistry] = []
#: Guards `_registries` and the handler's place. Reentrant: the handler runs on the main thread,
#: between any two lines that thread was running - `add` and `remove` included - and the
#: registries it stops take themselves off through `remove`.
_lock = threading.RLock()


def add(registry: JobRegistry) -> None:
    """Put a started registry under the hook, taking SIGTERM if nothing else handles it.

    Only from the main thread, the one CPython lets set a handler. Elsewhere the signal is left as
    it is, and :meth:`JobRegistry.start` warns if nobody handles it.
    """
    with _lock:
        if not any(known is registry for known in _registries):
            _registries.append(registry)
        if signal.getsignal(signal.SIGTERM) != signal.SIG_DFL:
            return  # this module's handler, taken for an earlier registry, or somebody else's
        if threading.current_thread() is not threading.main_thread():
            return
        signal.signal(signal.SIGTERM, _on_sigterm)
    LOG.info(
        "Nothing else in this process handles SIGTERM, so the JobRegistry does: on SIGTERM, "
        "running jobs finish, then the process stops. A SIGTERM handler set after this "
        "replaces it; register_shutdown_hook(False) turns it off."
    )


def remove(registry: JobRegistry) -> None:
    """Take a stopped registry off. The last one off gives SIGTERM back - to its default, which is
    what it was taken from - if the handler is still this module's and this is the main thread.

    Left in place off the main thread, the handler with no registry to stop does what the default
    does. Safe to call for a registry that was never added.
    """
    with _lock:
        _registries[:] = [known for known in _registries if known is not registry]
        if _registries or signal.getsignal(signal.SIGTERM) is not _on_sigterm:
            return
        if threading.current_thread() is not threading.main_thread():
            return
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _on_sigterm(number: int, frame: FrameType | None) -> None:
    # The default back first: a second SIGTERM while the jobs finish - from someone who will not
    # wait - stops the process at once, as it would have without this.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    registries = list(_registries)
    if registries:
        LOG.info("Received SIGTERM: letting running jobs finish before the process stops")
    # One after the other, here on the main thread. A thread started from here would wait for any
    # lock the interrupted main thread holds - logging's, for one - while the main thread waited
    # for it; on the main thread those locks are its own, and reentrant.
    for registry in registries:
        try:
            registry.stop()
        except BaseException:
            LOG.exception("Stopping %r on SIGTERM failed; stopping the process anyway", registry)
    signal.raise_signal(signal.SIGTERM)
    # Still alive: this process ignores SIGTERM's default action, as PID 1 in a container does.
    # End it as SIGTERM would have - no `finally`, no `atexit` - with the status a shell reports.
    os._exit(128 + signal.SIGTERM)


def _forget_after_fork() -> None:
    """In a child made by fork(): the registries are the parent's, with none of their threads, and
    the parent stops them. The handler stays, and with nothing to stop does what the default does.
    """
    global _lock
    _registries.clear()
    _lock = threading.RLock()  # another thread may have held it at the fork, and it stays held


if hasattr(os, "register_at_fork"):  # not on Windows, which has no fork
    os.register_at_fork(after_in_child=_forget_after_fork)
