"""N6-C e4: local-event-handler.md's Wiring snippet (lines 134-151), run as written on SQLite,
with only the names it assumes defined around it."""
import sys, tempfile, time
from pathlib import Path
from datetime import timedelta
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
from sqlalchemy import create_engine
from backends import start
from ekbatan.config import ShardGroupConfig, ShardingConfig, ShardMemberConfig
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier
from ekbatan.clock import SystemClock
from ekbatan.distributedjobs import job_registry
from ekbatan.events import (EventHandlerRegistry, LocalEventHandlerConfig, HandlingConfig,
                            EventFanoutJob, EventHandlingJob, EventEnvelope)
from ekbatan.events import EventHandler  # noqa: E402
from fixtures.logins import data_source
from fixtures.schema import EVENTS, EVENT_NOTIFICATIONS, metadata
from fixtures.domain import WalletMoneyDepositedEvent as WalletMoneyDepositedEvent

def p(k, v): print(f"PROBE {k}: {v}", flush=True)
b = start("sqlite", Path(tempfile.mkdtemp())); b.prepare("w", metadata)
S0 = ShardIdentifier(0, 0)
registry = DatabaseRegistry.from_config(ShardingConfig.builder().default_shard(S0).groups([
    ShardGroupConfig.builder().group(0).name("g").with_member(ShardMemberConfig.builder().member(0)
    .name("w").primary_config(data_source(b.url("w"), pool_size=5)).build()).build()]).build())
engine = registry.manager(S0).primary if hasattr(registry.manager(S0), "primary") else create_engine(b.url("w"))
clock = SystemClock(); executor = None

class WalletMoneyDepositedEventHandler(EventHandler[WalletMoneyDepositedEvent]):
    def name(self) -> str:
        return "money-deposited"

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent
    def __init__(self, executor) -> None:
        self.executor = executor
    def handle(self, envelope: EventEnvelope[WalletMoneyDepositedEvent]) -> None:
        event = envelope.event

# ---- as written ----
handlers = EventHandlerRegistry([WalletMoneyDepositedEventHandler(executor)])
events_config = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))

jobs = (
    job_registry()
    .data_source(engine)
    .with_jobs([
        EventFanoutJob(registry, handlers, events=EVENTS,
                       notifications=EVENT_NOTIFICATIONS, config=events_config, clock=clock),
        EventHandlingJob(registry, handlers,
                         notifications=EVENT_NOTIFICATIONS, config=events_config, clock=clock,
                         enabled=events_config.handling.enabled),
    ])
    .build()
)
jobs.start()
# ---- end ----
p("wiring.started", type(jobs).__name__)
time.sleep(1.5)
jobs.stop()
p("wiring.stopped", True)
b.stop()
