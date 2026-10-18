"""docs/wiring.md snippets: the keyword constructors it shows, and its registry claims, on SQLite."""
from decimal import Decimal
from common import probe, service
from wallet_service_sqlite.handlers import MoneyDepositedEventHandler
from wallet_service_sqlite.actions import DepositMoneyAction, OpenWalletAction
from wallet_service_sqlite.schema import EVENTS, EVENT_NOTIFICATIONS
from wallet_service_sqlite.jobs import PayStipendJob
from ekbatan import ActionRegistry, RepositoryRegistry, ActionExecutor
from ekbatan.events import EventHandlerRegistry, EventFanoutJob, EventHandlingJob
from ekbatan.distributedjobs import job_registry
from ekbatan.locks import PostgresKeyedLockProvider
from ekbatan.clock import SystemClock
svc = service(); reg = svc.registry; ex = svc.executor; running = SystemClock()
probe("wiring.md:229 EventHandlerRegistry([handler])", lambda: EventHandlerRegistry([MoneyDepositedEventHandler(ex)]))
handlers = EventHandlerRegistry.builder().with_handler(MoneyDepositedEventHandler(ex)).build()
from ekbatan import LocalEventHandlerConfig
cfg = LocalEventHandlerConfig()
probe("wiring.md:250 EventFanoutJob(registry, handlers, events=, notifications=, config=, clock=)", lambda: EventFanoutJob(reg, handlers, events=EVENTS, notifications=EVENT_NOTIFICATIONS, config=cfg, clock=running))
probe("wiring.md:252 EventHandlingJob(registry, handlers, notifications=, config=, clock=, enabled=)", lambda: EventHandlingJob(reg, handlers, notifications=EVENT_NOTIFICATIONS, config=cfg, clock=running, enabled=cfg.handling.enabled))
probe("wiring.md:246 PostgresKeyedLockProvider(engine) positional", lambda: PostgresKeyedLockProvider(reg.default.primary))
probe("wiring.md:258 job_registry().workers exists", lambda: hasattr(job_registry(), "workers"))
probe("wiring.md:307 duplicate action registration refused", lambda: ActionRegistry.builder().with_action(OpenWalletAction(running)).with_action(OpenWalletAction(running)).build())
probe("wiring.md:307 duplicate repository registration refused", lambda: RepositoryRegistry.builder().with_model_repository(svc.wallets).with_model_repository(svc.wallets).build())
probe("wiring.md:299 unregistered action fails at execute with a clear error", lambda: ActionExecutor(namespace="a.b", registry=reg, repositories=[svc.wallets], events=EVENTS).execute(OpenWalletAction, None))
probe("wiring.md:192 with_model_repository on an entity repo refused", lambda: RepositoryRegistry.builder().with_model_repository(svc.alerts).build())
svc.close()
svc2 = service()
from wallet_service_sqlite.repository import WalletRepository
probe("wiring.md:307 two different repositories for one type", lambda: RepositoryRegistry.builder().with_model_repository(svc2.wallets).with_model_repository(WalletRepository(svc2.registry)).build())
svc2.close()
