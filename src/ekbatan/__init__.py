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

from .accounting import StatementCount, count_statements
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
from .domain import Builder, Entity, GenericState, Model, ModelEvent, Persistable
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
from .events import (
    EventDispatch,
    EventEnvelope,
    EventFanout,
    EventHandler,
    EventHandlerRegistry,
    NotificationState,
)
from .jobs import Daily, DistributedJob, Every, FixedDelay, JobContext, JobRegistry, Schedule
from .locks import InProcessLock, KeyedLock, Lease, MariaDBLock, MySQLLock, PostgresLock
from .migrations import emit_ddl, migrate, migrate_registry
from .persistence import DatabaseRegistry, TransactionManager
from .repository import Repository
from .schema import notifications_table, outbox_table, scheduled_tasks_table
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
    "Builder",
    "Clock",
    "ConnectivityError",
    "CrossShardError",
    "Daily",
    "DataSourceConfig",
    "DatabaseRegistry",
    "DistributedJob",
    "EkbatanError",
    "EmbeddedBitsSharding",
    "Entity",
    "EntityNotFoundError",
    "EventDispatch",
    "EventEnvelope",
    "EventFanout",
    "EventHandler",
    "EventHandlerRegistry",
    "EventPersister",
    "Every",
    "ExecutionConfig",
    "FixedClock",
    "FixedDelay",
    "GenericState",
    "InProcessLock",
    "JobContext",
    "JobRegistry",
    "KeyedLock",
    "Lease",
    "LockError",
    "MariaDBLock",
    "Model",
    "ModelEvent",
    "MySQLLock",
    "NoSharding",
    "NotificationState",
    "OutboxRecord",
    "Persistable",
    "PostgresLock",
    "Repository",
    "RetryPolicy",
    "Schedule",
    "ShardConfig",
    "ShardIdentifier",
    "ShardingConfig",
    "ShardingStrategy",
    "SingleTableOutbox",
    "StaleRecordError",
    "StatementCount",
    "SystemClock",
    "TransactionManager",
    "UnsupportedError",
    "ValidationError",
    "__version__",
    "count_statements",
    "current_plan",
    "current_principal",
    "emit_ddl",
    "migrate",
    "migrate_registry",
    "notifications_table",
    "outbox_table",
    "scheduled_tasks_table",
    "sharded_uuid",
]
