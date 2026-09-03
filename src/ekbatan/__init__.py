"""Ekbatan: a persistence framework for event-driven systems.

A service that publishes domain events usually writes twice - once to its database, once to
its broker - and the two disagree whenever the second write fails. The transactional outbox is
the established answer, normally bolted on as a separate library or assembled by hand.

This folds it into the persistence layer, with three pieces:

- a **model** emits an event when it changes;
- an **action** stages changes on a **plan** instead of writing them;
- the **executor** opens one transaction, writes the rows and their events, and commits.

State and events land together or neither does, and no line of application code mentions an
outbox.
"""

from .action import (
    Action,
    ActionExecutor,
    ActionPlan,
    EventPersister,
    ExecutionConfig,
    OutboxRecord,
    RetryPolicy,
    SingleTableOutbox,
    current_plan,
    current_principal,
)
from .clock import Clock, FixedClock, SystemClock
from .config import DataSourceConfig, ShardConfig, ShardingConfig
from .domain import Entity, GenericState, Model, ModelEvent, Persistable
from .errors import (
    ConnectivityError,
    CrossShardError,
    EkbatanError,
    EntityNotFoundError,
    LockError,
    StaleRecordError,
    UnsupportedError,
    ValidationError,
)
from .persistence import DatabaseRegistry, TransactionManager
from .repository import Repository
from .sharding import (
    EmbeddedBitsSharding,
    NoSharding,
    ShardIdentifier,
    ShardingStrategy,
    sharded_uuid,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "Action",
    "ActionExecutor",
    "ActionPlan",
    "Clock",
    "ConnectivityError",
    "CrossShardError",
    "DataSourceConfig",
    "DatabaseRegistry",
    "EkbatanError",
    "EmbeddedBitsSharding",
    "Entity",
    "EntityNotFoundError",
    "EventPersister",
    "ExecutionConfig",
    "FixedClock",
    "GenericState",
    "LockError",
    "Model",
    "ModelEvent",
    "NoSharding",
    "OutboxRecord",
    "Persistable",
    "Repository",
    "RetryPolicy",
    "ShardConfig",
    "ShardIdentifier",
    "ShardingConfig",
    "ShardingStrategy",
    "SingleTableOutbox",
    "StaleRecordError",
    "SystemClock",
    "TransactionManager",
    "UnsupportedError",
    "ValidationError",
    "__version__",
    "current_plan",
    "current_principal",
    "sharded_uuid",
]
