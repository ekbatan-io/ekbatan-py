"""local-event-handler.md: 'A handling job you build runs ... a fixed delay of the handling poll
delay, 1 second by default, whatever the config says'; handling.enabled default off; the
configuration snippet's keywords and defaults. SQLite."""
from datetime import timedelta
from sqlalchemy import MetaData
from ekbatan.config import ShardingConfig
from ekbatan.persistence import DatabaseRegistry
from ekbatan.events.config import LocalEventHandlerConfig, HandlingConfig
from ekbatan.events.jobs import EventHandlingJob, EventFanoutJob
from ekbatan.events import EventHandlerRegistry
from ekbatan.schema import notifications_table, outbox_table
P = lambda k, v: print(f"PROBE {k}: {v}", flush=True)
reg = DatabaseRegistry.from_config(ShardingConfig.single("sqlite://"))
md = MetaData(); N = notifications_table(md); E = outbox_table(md)
h = EventHandlerRegistry([])
P("HandlingConfig() enabled default", HandlingConfig().enabled)
off = LocalEventHandlerConfig()
j = EventHandlingJob(reg, h, notifications=N, config=off)
P("handling job built by hand, config handling off -> schedule", repr(j.schedule()))
j2 = EventHandlingJob(reg, h, notifications=N, config=off, enabled=off.handling.enabled)
P("enabled=config.handling.enabled -> schedule", type(j2.schedule()).__name__)
cfg = LocalEventHandlerConfig(fanout_poll_delay=timedelta(seconds=1), fanout_batch_size=200, handling_poll_delay=timedelta(seconds=1),
    handling_batch_size=100, handling_max_backoff_cap=timedelta(minutes=5), handling_retention_window=timedelta(days=7),
    handling=HandlingConfig(enabled=True))
P("doc's configuration snippet builds", True)
P("fan-out job schedule by default", repr(EventFanoutJob(reg, h, events=E, notifications=N).schedule()))
