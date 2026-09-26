"""The local event handler: reacting to your own events without a broker."""

from .config import (
    HandlingConfig,
    HandlingConfigBuilder,
    LocalEventHandlerConfig,
    LocalEventHandlerConfigBuilder,
)
from .dispatch import EventDispatch
from .fanout import EventFanout
from .handler import (
    EventEnvelope,
    EventEnvelopeBuilder,
    EventHandler,
    EventHandlerRegistry,
    EventHandlerRegistryBuilder,
)
from .jobs import EventFanoutJob, EventHandlingJob
from .notification import EventNotificationState, backoff

__all__ = [
    "EventDispatch",
    "EventEnvelope",
    "EventEnvelopeBuilder",
    "EventFanout",
    "EventFanoutJob",
    "EventHandler",
    "EventHandlerRegistry",
    "EventHandlerRegistryBuilder",
    "EventHandlingJob",
    "EventNotificationState",
    "HandlingConfig",
    "HandlingConfigBuilder",
    "LocalEventHandlerConfig",
    "LocalEventHandlerConfigBuilder",
    "backoff",
]
