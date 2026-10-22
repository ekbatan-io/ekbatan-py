"""Running the sync suite's scenarios on `ekbatan.aio` too - the harness behind `mode`.

A test module that sets ``MODES = ("sync", "async")`` runs each of its tests twice. In the async
run every async object the test touches is the real `ekbatan.aio` one, running on a real event
loop over the database's async driver (asyncpg, aiomysql, aiosqlite) - only the test body is
not a coroutine. It calls through a **portal**: the event loop runs on a thread of its own, and
each call the test makes is sent there as a task and waited for. That is anyio's
``BlockingPortal``, written out here because anyio is not a dependency of the suite.

How a test body written against the sync API reaches the async one:

- **names**. For the async run, each name in the test module (and in the support modules listed
  in ``SUPPORT_MODULES``) that has an async twin is rebound to it: ``WalletRepository`` becomes
  the async wallet repository, ``Action`` becomes ``AsyncAction``, ``engine_for`` becomes
  ``async_engine_for``. A test module adds twins of its own with :func:`twin`.
- **calls**. What an async object hands back is made blocking: a coroutine is awaited on the
  loop, an ``async with`` block becomes a ``with`` block, an async iterator a plain one, and
  another async object is wrapped the same way. Rows, models and results come back as they are.
- **context**. Each thread that calls in is one task on the loop for all its calls - a task
  started on its first call, with a context of its own, that runs each call it is sent in turn.
  So an ambient transaction opened by one call is seen by the next, a keyed lock taken by one
  call is re-entered by the next, exactly as within one task - and two threads calling in are
  two tasks that never see each other's.
- **actions**. An action subclassed in a test from the rebound ``Action`` with a plain ``def
  perform`` gets an ``async def perform`` that runs it on the loop. It may stage and read the
  clock; one that calls a repository needs a real async twin (:func:`twin`), and says so loudly
  if it lacks one - a call into the portal from the loop's own thread is refused.

What this harness cannot express is tested natively, in `test_async.py` and
`test_async_parity.py`: tasks interleaving, cancellation, timeouts, the pool cap, concurrent
shard reads.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import inspect
import threading
import types
from collections.abc import Callable, Iterator
from typing import Any

from ekbatan.aio.locks.base import Acquiring

#: Modules whose names are rebound for the async run, besides the test module itself: support
#: code a test calls, which builds executors and repositories of its own.
SUPPORT_MODULES = ("fixtures.executing",)

#: Sync class or function -> its async twin.
TWINS: dict[Any, Any] = {}


def twin(sync: Any) -> Callable[[Any], Any]:
    """Register the decorated class (or function) as the async twin of ``sync``."""

    def register(async_twin: Any) -> Any:
        TWINS[sync] = async_twin
        return async_twin

    return register


def rebased(sync_class: type) -> type:
    """Decorate a test's own repository class to give it an async twin: the same body on the
    async twin of its base. A method of its own written against the sync API - a query on
    ``self.readonly_db()`` - runs, when a test calls it, with ``self`` made blocking.

    The body is copied, not inherited, so a method that calls ``super()`` without arguments
    would find the sync class; none here does."""
    bases = tuple(TWINS.get(base, base) for base in sync_class.__bases__)
    namespace: dict[str, Any] = {}
    for name, value in vars(sync_class).items():
        if name in ("__dict__", "__weakref__"):
            continue
        if (
            inspect.isfunction(value)
            and not name.startswith("__")
            and name not in _MAPPING
            and not inspect.iscoroutinefunction(value)
        ):
            value = _blocking_self(value)
        namespace[name] = value
    TWINS[sync_class] = type(sync_class.__name__, bases, namespace)
    return sync_class


def stager_twin(sync_action: type) -> type:
    """Make and register the async twin of an action that only stages, raises and reads the
    clock: a subclass of both the sync action and ``AsyncAction``, whose ``perform`` is the sync
    one's run as a coroutine that awaits nothing. Its constructor and name are the sync one's.
    An action that reads a repository needs a twin written out, with the awaits."""
    from ekbatan.aio import AsyncAction

    perform = sync_action.perform  # type: ignore[attr-defined]

    async def awaited(self: Any, principal: Any, params: Any) -> Any:
        return perform(self, principal, params)

    made = type(
        f"Async{sync_action.__name__}",
        (sync_action, AsyncAction),
        {
            "perform": awaited,
            "name": property(lambda self: sync_action.__name__),
            "__module__": sync_action.__module__,
        },
    )
    TWINS[sync_action] = made
    return made


def _register_framework_twins() -> None:
    from ekbatan import testing
    from ekbatan.action import (
        Action,
        ActionExecutor,
        ActionExecutorBuilder,
        ActionRegistry,
        ChangePersister,
        SingleTableJsonEventPersister,
    )
    from ekbatan.action.outbox import EventEntityRepository
    from ekbatan.aio import (
        AbstractAsyncRepository,
        AsyncAction,
        AsyncActionExecutor,
        AsyncActionExecutorBuilder,
        AsyncActionSpec,
        AsyncChangePersister,
        AsyncDatabaseRegistry,
        AsyncDatabaseRegistryBuilder,
        AsyncDb,
        AsyncEntityRepository,
        AsyncModelRepository,
        AsyncSingleTableJsonEventPersister,
        AsyncTransaction,
        AsyncTransactionManager,
        async_engine_for,
        async_run_action,
    )
    from ekbatan.aio.outbox import AsyncEventEntityRepository
    from ekbatan.fetching import Db
    from ekbatan.persistence import (
        DatabaseRegistry,
        DatabaseRegistryBuilder,
        Transaction,
        TransactionManager,
        engine_for,
    )
    from ekbatan.repository import (
        AbstractRepository,
        EntityRepository,
        ModelRepository,
        RepositoryRegistry,
    )

    TWINS.update(
        {
            # Shared by both kinds, and rebound to themselves: what they hold comes back made
            # blocking, as `executor.repositories` does.
            ActionRegistry: ActionRegistry,
            RepositoryRegistry: RepositoryRegistry,
            Action: AsyncAction,
            AbstractRepository: AbstractAsyncRepository,
            ModelRepository: AsyncModelRepository,
            EntityRepository: AsyncEntityRepository,
            ActionExecutor: AsyncActionExecutor,
            ActionExecutorBuilder: AsyncActionExecutorBuilder,
            DatabaseRegistry: AsyncDatabaseRegistry,
            DatabaseRegistryBuilder: AsyncDatabaseRegistryBuilder,
            TransactionManager: AsyncTransactionManager,
            Transaction: AsyncTransaction,
            Db: AsyncDb,
            SingleTableJsonEventPersister: AsyncSingleTableJsonEventPersister,
            EventEntityRepository: AsyncEventEntityRepository,
            ChangePersister: AsyncChangePersister,
            engine_for: async_engine_for,
            testing.ActionSpec: AsyncActionSpec,
            testing.run_action: async_run_action,
        }
    )


_register_framework_twins()


def _register_lock_twins() -> None:
    """The keyed lock providers: each sync provider's twin is the async one over the same
    backend. Redis only when redis-py is installed, as the Redis tests skip without it."""
    from ekbatan.aio.locks import (
        AsyncMariaDBKeyedLockProvider,
        AsyncMySQLKeyedLockProvider,
        AsyncPostgresKeyedLockProvider,
    )
    from ekbatan.locks import (
        MariaDBKeyedLockProvider,
        MySQLKeyedLockProvider,
        PostgresKeyedLockProvider,
    )

    TWINS.update(
        {
            PostgresKeyedLockProvider: AsyncPostgresKeyedLockProvider,
            MySQLKeyedLockProvider: AsyncMySQLKeyedLockProvider,
            MariaDBKeyedLockProvider: AsyncMariaDBKeyedLockProvider,
        }
    )
    try:
        import redis  # noqa: F401
    except ImportError:  # pragma: no cover - the dev group installs it
        return
    from ekbatan.aio.locks.redis import AsyncRedisKeyedLockProvider
    from ekbatan.locks.redis import RedisKeyedLockProvider

    TWINS[RedisKeyedLockProvider] = AsyncRedisKeyedLockProvider


_register_lock_twins()


def _register_event_twin() -> None:
    """``sqlalchemy.event`` for an async engine: SQLAlchemy fires an async engine's events on
    the sync engine it wraps, and says to listen there - so the twin does."""
    import types

    from sqlalchemy import event

    def target(engine: Any) -> Any:
        engine = unwrap(engine)
        return getattr(engine, "sync_engine", engine)

    shim = types.SimpleNamespace(
        listen=lambda t, *a, **k: event.listen(target(t), *a, **k),
        remove=lambda t, *a, **k: event.remove(target(t), *a, **k),
        contains=lambda t, *a, **k: event.contains(target(t), *a, **k),
        listens_for=lambda t, *a, **k: event.listens_for(target(t), *a, **k),
    )
    TWINS[event] = shim


_register_event_twin()


def async_twin_url(url: Any) -> Any:
    """A url for the same database through its default async driver - for a test that builds
    an engine of its own from a container's url."""
    from sqlalchemy.engine import make_url

    from ekbatan.aio import ASYNC_DEFAULT_DRIVERS
    from ekbatan.config import _DIALECTS

    parsed = make_url(url)
    backend = parsed.get_backend_name()
    return parsed.set(drivername=f"{backend}+{ASYNC_DEFAULT_DRIVERS[_DIALECTS[backend]]}")


def _register_engine_twin() -> None:
    import sqlalchemy
    from sqlalchemy.ext.asyncio import create_async_engine

    def create_engine(url: Any, **kwargs: Any) -> Any:
        return create_async_engine(async_twin_url(url), **kwargs)

    TWINS[sqlalchemy.create_engine] = create_engine


_register_engine_twin()


# ------------------------------------------------------------------------------- the portal


class PortalReentryError(RuntimeError):
    """A blocking call made from the event loop's own thread - it would wait for itself."""


class Portal:
    """An event loop on a thread of its own, and blocking calls into it."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="ekbatan-portal", daemon=True)
        self._runners: dict[int, tuple[asyncio.Queue[Any], asyncio.Task[None]]] = {}
        self._proxies: dict[int, tuple[Any, Proxy]] = {}
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _runner(self, ident: int, context: contextvars.Context) -> asyncio.Queue[Any]:
        """The calling thread's task, started on its first call: it awaits each call it is sent,
        one after another, so every call a thread makes runs in one task and one context."""
        held = self._runners.get(ident)
        if held is not None:
            return held[0]
        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def run() -> None:
            while True:
                awaitable, done = await queue.get()
                try:
                    result = await awaitable
                except BaseException as error:  # handed to the caller, whatever it is
                    task = asyncio.current_task()
                    while task is not None and task.cancelling():
                        task.uncancel()
                    if isinstance(error, asyncio.CancelledError):
                        done.set_exception(asyncio.CancelledError())
                    else:
                        done.set_exception(error)
                else:
                    done.set_result(result)

        task = self.loop.create_task(run(), context=context)
        self._runners[ident] = (queue, task)
        return queue

    def call(self, awaitable: Any) -> Any:
        """Run ``awaitable`` on the loop, in the calling thread's task; wait."""
        if threading.current_thread() is self._thread:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise PortalReentryError(
                "a blocking call from inside the event loop: this code runs on the loop and "
                "calls the async API as if it were sync. Give it an async twin (modes.twin)."
            )
        ident = threading.get_ident()
        # The calling thread's context, for its task's first call - as a task started from
        # this thread would copy it.
        context = None if ident in self._runners else contextvars.copy_context()
        done: concurrent.futures.Future[Any] = concurrent.futures.Future()

        def start() -> None:
            self._runner(ident, context or contextvars.copy_context()).put_nowait((awaitable, done))

        self.loop.call_soon_threadsafe(start)
        return done.result()

    def call_function(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call a plain function on the loop, in the calling thread's context."""
        if threading.current_thread() is self._thread:
            return function(*args, **kwargs)

        async def run() -> Any:
            return function(*args, **kwargs)

        return self.call(run())

    def close(self) -> None:
        async def stop_runners() -> None:
            tasks = [task for _, task in self._runners.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if self._runners:
            asyncio.run_coroutine_threadsafe(stop_runners(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(10)
        self.loop.close()
        self._proxies.clear()

    # ------------------------------------------------------------ making things blocking

    def wrap(self, value: Any) -> Any:
        """``value`` as a test written against the sync API can use it."""
        if isinstance(value, Proxy | type):
            return value
        # A keyed lock's `acquire`: awaited at once, as the sync `acquire` takes the lock at
        # once. The lease is an async context manager, so `with locks.acquire(...)` still works.
        if isinstance(value, Acquiring):
            return self.wrap(self.call(value))
        # An async context manager before an awaitable: `engine.connect()` is both, and a test
        # enters it with `with` - awaiting it first would start the connection twice.
        if hasattr(value, "__anext__") or (
            hasattr(value, "__aenter__") and not hasattr(value, "__enter__")
        ):
            return self._proxy(value)
        if inspect.isawaitable(value):
            return self.wrap(self.call(value))
        if inspect.ismethod(value) or inspect.isfunction(value) or inspect.isbuiltin(value):
            return _Blocking(self, value)
        if type(value) is tuple:
            return tuple(self.wrap(v) for v in value)
        if _is_inert(value):
            return value
        if _is_async_kind(value) or isinstance(value, _containers()):
            return self._proxy(value)
        return value

    def _proxy(self, value: Any) -> Proxy:
        """One proxy per object, so ``a is b`` means what it does on the objects themselves."""
        held = self._proxies.get(id(value))
        if held is not None and held[0] is value:
            return held[1]
        made = Proxy(self, value)
        self._proxies[id(value)] = (value, made)
        return made


#: The portal of the test running now - set by the `portal` fixture, read by portaled classes.
CURRENT: list[Portal] = []


def current() -> Portal:
    if not CURRENT:
        raise RuntimeError("no portal: this async object was used outside an async-mode test")
    return CURRENT[-1]


def _containers() -> tuple[type, ...]:
    """Sync framework objects that hold async ones and hand them back - proxied, so what they
    hand back is made blocking and what goes in is unwrapped."""
    from ekbatan.action import ActionRegistry, ActionRegistryBuilder
    from ekbatan.repository import RepositoryRegistry, RepositoryRegistryBuilder

    return (ActionRegistry, ActionRegistryBuilder, RepositoryRegistry, RepositoryRegistryBuilder)


def unwrap_on_add(monkeypatch: Any) -> None:
    """Hand the framework's own registries the real objects, never a proxy: a proxy reached
    from inside the loop would block the loop on itself."""
    from ekbatan.action import ActionRegistry
    from ekbatan.repository import RepositoryRegistry

    for registry in (ActionRegistry, RepositoryRegistry):
        original = registry._add

        def _add(self: Any, item: Any, _original: Any = original) -> None:
            _original(self, unwrap(item))

        monkeypatch.setattr(registry, "_add", _add)


def _is_inert(value: Any) -> bool:
    """An async object a test never calls into: an action. Handed over as it is, so ``type()``
    of it - which a proxy cannot fake - is its own class, as the executor files it."""
    from ekbatan.action.action import _ActionCore

    return isinstance(value, _ActionCore)


def _is_async_kind(value: Any) -> bool:
    return any(
        (getattr(cls, "__module__", "") or "").startswith(("ekbatan.aio", "sqlalchemy.ext.asyncio"))
        for cls in type(value).__mro__
    )


def unwrap(value: Any) -> Any:
    """The real object behind a proxy, and the async class behind a portaled one - inside
    containers too, as a test hands over ``repositories=[wallets]``."""
    if isinstance(value, Proxy):
        return object.__getattribute__(value, "_target")
    if isinstance(value, type):
        # Its own attribute only: a test's subclass of a portaled class is itself.
        return vars(value).get("__portaled_base__") or value
    if isinstance(value, list):
        return [unwrap(v) for v in value]
    if isinstance(value, tuple) and type(value) is tuple:
        return tuple(unwrap(v) for v in value)
    if isinstance(value, dict) and type(value) is dict:
        return {k: unwrap(v) for k, v in value.items()}
    return value


class _Blocking:
    """A method of an async object, made blocking."""

    __slots__ = ("_portal", "_target")

    def __init__(self, portal: Portal, target: Callable[..., Any]) -> None:
        self._portal = portal
        self._target = target

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        target, portal = self._target, self._portal
        args, kwargs = unwrap(args), unwrap(kwargs)
        if _is_test_method(target) or inspect.iscoroutinefunction(target):
            # A test's own method runs where the test does; a coroutine is created here and
            # awaited on the loop by `wrap`.
            return portal.wrap(target(*args, **kwargs))
        # A plain method runs on the loop, in the calling thread's context - the one its tasks
        # run in - so one that reads the ambient transaction (`tx_db()`, `current()`) sees what
        # the last call opened.
        return portal.wrap(portal.call_function(target, *args, **kwargs))

    def __repr__(self) -> str:
        return repr(self._target)

    @property
    def __wrapped__(self) -> Callable[..., Any]:
        """The method itself, as ``functools.wraps`` says it: ``rows(...).fetch(self.from_record)``
        reads the mapper's record class off what it wraps, as off any decorated function."""
        return self._target


class Proxy:
    """An async object, used as if it were sync."""

    __slots__ = ("_portal", "_target")

    def __init__(self, portal: Portal, target: Any) -> None:
        object.__setattr__(self, "_portal", portal)
        object.__setattr__(self, "_target", target)

    @property  # type: ignore[misc]
    def __class__(self) -> type:  # isinstance() sees the real class
        return type(object.__getattribute__(self, "_target"))

    def __getattr__(self, name: str) -> Any:
        target = object.__getattribute__(self, "_target")
        portal: Portal = object.__getattribute__(self, "_portal")
        return portal.wrap(getattr(target, name))

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_target"), name, unwrap(value))

    def _call(self, name: str, *args: Any) -> Any:
        target = object.__getattribute__(self, "_target")
        portal: Portal = object.__getattribute__(self, "_portal")
        return portal.wrap(getattr(target, name)(*unwrap(args)))

    def __enter__(self) -> Any:
        target = object.__getattribute__(self, "_target")
        if hasattr(target, "__aenter__"):
            return self._call("__aenter__")
        return self._call("__enter__")

    def __exit__(self, *exc: Any) -> Any:
        target = object.__getattribute__(self, "_target")
        if hasattr(target, "__aexit__"):
            return self._call("__aexit__", *exc)
        return self._call("__exit__", *exc)

    def __iter__(self) -> Iterator[Any]:
        target = object.__getattribute__(self, "_target")
        portal: Portal = object.__getattribute__(self, "_portal")
        if hasattr(target, "__anext__") or hasattr(target, "__aiter__"):
            iterator = target.__aiter__() if hasattr(target, "__aiter__") else target
            finished = False
            try:
                while True:
                    try:
                        item = portal.call(iterator.__anext__())
                    except StopAsyncIteration:
                        finished = True
                        return
                    yield portal.wrap(item)
            finally:
                # Stopped early: close it on the loop, where its `async with` blocks can exit.
                if not finished and hasattr(iterator, "aclose"):
                    portal.call(iterator.aclose())
        else:
            for item in target:
                yield portal.wrap(item)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        target = object.__getattribute__(self, "_target")
        portal: Portal = object.__getattribute__(self, "_portal")
        return portal.wrap(target(*unwrap(args), **unwrap(kwargs)))

    def __getitem__(self, key: Any) -> Any:
        return self._call("__getitem__", key)

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_target"))

    def __contains__(self, item: Any) -> bool:
        return unwrap(item) in object.__getattribute__(self, "_target")

    def __bool__(self) -> bool:
        return bool(object.__getattribute__(self, "_target"))

    def __eq__(self, other: object) -> bool:
        return bool(object.__getattribute__(self, "_target") == unwrap(other))

    def __hash__(self) -> int:
        return hash(object.__getattribute__(self, "_target"))

    def __repr__(self) -> str:
        return repr(object.__getattribute__(self, "_target"))

    def __str__(self) -> str:
        return str(object.__getattribute__(self, "_target"))


# ----------------------------------------------------------------- portaled classes


_PORTALED: dict[Any, Any] = {}


def portaled(async_twin: Any) -> Any:
    """What a twin is rebound as: a class whose instances, and whose class methods' results,
    come back made blocking - and which a test may subclass as it subclassed the sync one."""
    if not isinstance(async_twin, type):
        if callable(async_twin):
            return _portaled_function(async_twin)
        return async_twin
    made = _PORTALED.get(async_twin)
    if made is not None:
        return made

    base = async_twin

    def __new__(cls: type, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = unwrap(args), unwrap(kwargs)
        if cls is made:
            return current().wrap(base(*args, **kwargs))
        made_new = base.__new__
        instance = (
            object.__new__(cls) if made_new is object.__new__ else made_new(cls, *args, **kwargs)
        )
        instance.__init__(*args, **kwargs)
        return current().wrap(instance)

    def __init_subclass__(cls: type, **kwargs: Any) -> None:
        # A test's own action, subclassed from the rebound `Action` with a plain `perform`.
        perform = cls.__dict__.get("perform")
        if perform is not None and not inspect.iscoroutinefunction(perform):

            async def awaited(self: Any, principal: Any, params: Any) -> Any:
                return perform(self, principal, params)

            cls.perform = awaited  # type: ignore[attr-defined]
        # A test's own repository method, written against the sync API - a query of its own on
        # `self.readonly_db()`. Called from the test, it runs with `self` made blocking too.
        for name, function in list(vars(cls).items()):
            if (
                inspect.isfunction(function)
                and not name.startswith("__")
                and name not in _MAPPING
                and not inspect.iscoroutinefunction(function)
            ):
                setattr(cls, name, _blocking_self(function))

    namespace: dict[str, Any] = {
        "__new__": __new__,
        "__init_subclass__": classmethod(__init_subclass__),
        # Subscripting keeps its arguments, so `AbstractRepository[Wallet, WalletsRecord, ...]`
        # in a test still names its record class - the framework reads it off the bases, and
        # `__orig_bases__` below carries it on to the async class's own type parameters.
        "__class_getitem__": classmethod(lambda cls, params: types.GenericAlias(cls, params)),
        "__portaled_base__": base,
        "__module__": base.__module__,
        "__qualname__": base.__qualname__,
    }
    type_params = tuple(getattr(base, "__type_params__", ()) or ())
    if type_params:
        namespace["__orig_bases__"] = (base[type_params],)
    for name in ("builder", "from_config", "of"):
        if isinstance(inspect.getattr_static(base, name, None), classmethod):
            namespace[name] = _portaled_classmethod(base, name)

    class _Meta(type(base)):  # type: ignore[misc]
        def __instancecheck__(cls, instance: Any) -> bool:
            # The rebound name stands for the async class: `isinstance(tx, Transaction)` holds
            # for the async transaction. Read off the MRO, never through issubclass - ABCMeta's
            # subclass check walks a class's subclasses, and this class is one of them.
            if cls is made:
                return base in getattr(instance, "__class__", type(instance)).__mro__
            return super().__instancecheck__(instance)

    made = _Meta(base.__name__, (base,), namespace)
    _PORTALED[async_twin] = made
    return made


#: Methods the framework calls itself, on the loop: never given a blocking `self`.
_MAPPING = frozenset({"to_record", "from_record", "perform", "shard_for", "shard_of"})


def _blocking_self(function: Callable[..., Any]) -> Callable[..., Any]:
    def method(self: Any, *args: Any, **kwargs: Any) -> Any:
        portal = current()
        if threading.current_thread() is portal._thread:
            return function(self, *args, **kwargs)
        return function(portal.wrap(self), *args, **kwargs)

    method.__name__ = function.__name__
    method.__wrapped__ = function  # type: ignore[attr-defined]
    method.__ekbatan_test_method__ = True  # type: ignore[attr-defined]
    return method


def _is_test_method(target: Any) -> bool:
    function = getattr(target, "__func__", target)
    return bool(getattr(function, "__ekbatan_test_method__", False))


def _portaled_classmethod(base: type, name: str) -> Any:
    def call(cls: type, *args: Any, **kwargs: Any) -> Any:
        return current().wrap(getattr(base, name)(*unwrap(args), **unwrap(kwargs)))

    return classmethod(call)


def _portaled_function(function: Callable[..., Any]) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        return current().wrap(function(*unwrap(args), **unwrap(kwargs)))

    call.__name__ = getattr(function, "__name__", "function")
    call.__wrapped__ = function  # type: ignore[attr-defined]
    return call


def rebind(module: Any, monkeypatch: Any) -> None:
    """Rebind every name in ``module`` that has an async twin."""
    for name, value in list(vars(module).items()):
        try:
            found = value in TWINS
        except TypeError:  # unhashable
            continue
        if found:
            monkeypatch.setattr(module, name, portaled(TWINS[value]))
