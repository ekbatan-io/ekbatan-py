"""Draining on SIGTERM, without taking the signal away from the application.

Java's `JobRegistry` calls `registerShutdownHook(true)`, and a JVM shutdown hook is additive:
any number can be registered, they all run, and none displaces another. That is why Java can
turn it on by default and nobody minds.

CPython has one handler slot per signal. `signal.signal()` **replaces** what is there and hands
it back - verified by running, not assumed - so a library that installs one silently is taking
the application's SIGTERM away from it. Hence two rules here:

**It is off unless asked.** `register_shutdown_hook(True)` is a sentence the application writes,
so nothing about its process changes without it saying so.

**What was there still runs.** The previous handler is kept and called afterwards. Where there
was none, the default is put back and the signal re-raised, so the process still dies exactly as
it would have - a hook that drained cleanly and then left the process alive would be a worse bug
than the one it fixes.

`atexit` cannot do this part: it does not fire on SIGTERM at all - measured, and not under
uvicorn either - which is the case that matters: a container stop, a Kubernetes rollout, a
`docker stop`. It does the other part. A started `JobRegistry` is on the `atexit` list by
default, which covers a normal exit and Ctrl-C and, being additive, takes nothing from anyone.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable
from types import FrameType

from ..errors import ValidationError

LOG = logging.getLogger("io.ekbatan.distributedjobs.JobRegistry")

#: What a container stop sends, and what an interactive Ctrl-C sends. Both mean "wind up".
_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class ShutdownHook:
    """Runs ``drain`` when the process is asked to stop, then gets out of the way."""

    __slots__ = ("_drain", "_installed", "_previous")

    def __init__(self, drain: Callable[[], None]) -> None:
        self._drain = drain
        self._previous: dict[int, object] = {}
        self._installed = False

    def install(self) -> None:
        """Take the signals, keeping what was there.

        Refuses off the main thread rather than doing nothing: `signal.signal` raises there, and
        a hook that quietly failed to install would be discovered during an outage, by its
        absence.
        """
        if self._installed:
            return
        if threading.current_thread() is not threading.main_thread():
            raise ValidationError(
                "register_shutdown_hook() needs the main thread - CPython only allows a signal "
                "handler to be installed there. Build the JobRegistry on the main thread, or "
                "leave the hook off and call JobRegistry.stop() from wherever your application "
                "already handles shutdown."
            )
        for number in _SIGNALS:
            self._previous[number] = signal.getsignal(number)
            signal.signal(number, self._handle)
        self._installed = True
        LOG.info("shutdown hook installed on SIGTERM and SIGINT")

    def remove(self) -> None:
        """Put back exactly what was found. Safe to call twice."""
        if not self._installed:
            return
        for number, previous in self._previous.items():
            if previous is not None:
                signal.signal(number, previous)  # type: ignore[arg-type]
        self._previous.clear()
        self._installed = False

    def _handle(self, number: int, frame: FrameType | None) -> None:
        previous = self._previous.get(number)
        try:
            self._drain()
        except BaseException:
            # Never leave the process alive because draining failed. The point of the hook is
            # that the process still ends the way the operator asked it to.
            LOG.exception("the shutdown hook failed to drain; continuing with the shutdown")

        if callable(previous):
            previous(number, frame)  # the application's own handler, unchanged
            return
        if previous is signal.SIG_IGN:
            return  # it was being ignored before us, and still should be
        # There was nothing but the default. Put it back and re-raise, so the process dies of
        # this signal rather than surviving it - which is what every caller expects.
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)
