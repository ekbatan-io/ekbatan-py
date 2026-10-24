"""The two refusals: an object used in a process it was copied into, and the sync API on an
event loop's thread.

Both refuse and explain; neither fixes anything. A copied pool is not reset, a blocking call is
not moved to a thread - doing either behind the caller's back would hide a mistake in how the
application starts or where it calls from, and the fix is one line in the application.
"""

from __future__ import annotations

import os
import sys
import threading

from .errors import BlockingCallError, ForkedProcessError

__all__ = ["refuse_if_forked", "refuse_on_an_event_loop"]


def refuse_if_forked(created_in: int, what: str) -> None:
    """Refuse ``what`` in any process but ``created_in``, the one that built it.

    ``fork()`` copies an object's pools and threads' bookkeeping into the child, but not the
    threads, and the connections it copies are the parent's sockets: two processes talking on
    one connection corrupt each other's conversations.
    """
    if os.getpid() != created_in:
        raise ForkedProcessError(
            f"{what} was created in another process and copied into this one by fork() - "
            f"gunicorn --preload, uwsgi, Celery prefork, RQ and multiprocessing all fork. Its "
            f"connections and threads belong to the process that created it. Create ekbatan's "
            f"objects - data sources, the database registry, the job registry, lock providers - "
            f"in your app's startup code (the FastAPI lifespan, or the top of the worker's "
            f"main()), not at import time."
        )


def refuse_on_an_event_loop(what: str) -> None:
    """Refuse ``what`` - a sync call that waits on a database - on an asyncio event loop's
    thread, where it would stop every other task on the loop until it returns.

    asyncio not imported means no loop can be running, so then this costs a dictionary lookup.
    A worker thread (``asyncio.to_thread``, ``run_in_executor``, a FastAPI ``def`` endpoint)
    has no running loop. Under gevent a worker "thread" is a greenlet on the loop's own OS
    thread, so asyncio reports the loop as running there too; the loop records the thread - the
    greenlet, under gevent - that runs it, and only that one is refused. There a blocking call
    yields, as every call does under gevent.
    """
    asyncio = sys.modules.get("asyncio")
    if asyncio is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    runner = getattr(loop, "_thread_id", None)
    if runner is not None and runner != threading.get_ident():
        return
    raise BlockingCallError(
        f"{what} was called on an asyncio event loop's thread (inside `async def`). The sync "
        f"API waits on the database, and here it would stop the event loop and every request "
        f"on it. In `async def` code use ekbatan.aio; or make the endpoint a plain `def`; or "
        f"call it with `await asyncio.to_thread(...)`."
    )
