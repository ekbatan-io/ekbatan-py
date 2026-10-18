"""concepts/sharding.md:113 says the fan-out round visits shards one at a time. Run a round on two
shards whose drain sleeps 0.5 s and see whether it takes ~0.5 s (concurrent) or ~1 s (sequential)."""
import tempfile, time, threading
from common import probe
from wallet_service_sqlite.schema import EVENTS, EVENT_NOTIFICATIONS, metadata
from wallet_service_sqlite.handlers import MoneyDepositedEventHandler
from ekbatan import (ShardingConfig, ShardGroupConfig, ShardMemberConfig, DataSourceConfig, DatabaseRegistry, ShardIdentifier)
from ekbatan.events import EventHandlerRegistry
from ekbatan.events.fanout import EventFanout
d = tempfile.mkdtemp()
reg = DatabaseRegistry.from_config(ShardingConfig(default_shard=ShardIdentifier(0, 0), groups=tuple(
    ShardGroupConfig(group=g, name=f"g{g}", members=(ShardMemberConfig(member=0, configs={"primaryConfig": DataSourceConfig(url=f"sqlite:///{d}/{g}.db")}),)) for g in (0, 1))))
handlers = EventHandlerRegistry([MoneyDepositedEventHandler(None)])
seen = []
class Slow(EventFanout):
    __slots__ = ()
    def _drain_shard(self, shard):
        seen.append(threading.current_thread().name); time.sleep(0.5); return 0
f = Slow(reg, handlers, events=EVENTS, notifications=EVENT_NOTIFICATIONS)
t0 = time.monotonic(); f.drain_once(); el = time.monotonic() - t0
probe("fan-out round on 2 shards, 0.5 s each: elapsed s", lambda: round(el, 2))
probe("threads used", lambda: seen)
f.close(); reg.close()
