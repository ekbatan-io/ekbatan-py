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
    DEFAULT_RETRIES,
    Action,
    ActionExecutor,
    ActionPlan,
    EventPersister,
    ExecutionConfiguration,
    OutboxRecord,
    RetryConfig,
    SingleTableJsonEventPersister,
    current_clock,
    current_plan,
    current_principal,
)
from .clock import Clock, SystemClock, VirtualClock
from .config import DataSourceConfig, ShardConfig, ShardingConfig
from .domain import (
    Entity,
    EntityBuilder,
    GenericState,
    Model,
    ModelBuilder,
    ModelEvent,
    Persistable,
)
from .errors import (
    ConnectivityError,
    CrossShardError,
    EkbatanError,
    EntityNotFoundError,
    LockAcquisitionError,
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
    EventNotificationState,
)
from .ids import Id, ShardedId, TypedValue, unwrap
from .jobs import Daily, DistributedJob, Every, FixedDelay, JobContext, JobRegistry, Schedule
from .locks import (
    InProcessKeyedLockProvider,
    KeyedLockProvider,
    Lease,
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
)
from .migrations import emit_ddl, migrate, migrate_registry
from .persistence import DatabaseRegistry, TransactionManager
from .repository import EntityRepository, ModelRepository, Repository
from .schema import notifications_table, outbox_table, scheduled_tasks_table
from .sharding import (
    DEFAULT_SHARD,
    EmbeddedBitsShardingStrategy,
    NoShardingStrategy,
    ShardIdentifier,
    ShardingStrategy,
    read_shard,
    sharded_uuid,
    uuid_instant,
)
from .testing import StagedWork, run_action

__version__ = "0.1.0.dev0"

__all__ = [
    "DEFAULT_RETRIES",
    "DEFAULT_SHARD",
    "Action",
    "ActionExecutor",
    "ActionPlan",
    "Clock",
    "ConnectivityError",
    "CrossShardError",
    "Daily",
    "DataSourceConfig",
    "DatabaseRegistry",
    "DistributedJob",
    "EkbatanError",
    "EmbeddedBitsShardingStrategy",
    "Entity",
    "EntityBuilder",
    "EntityNotFoundError",
    "EntityRepository",
    "EventDispatch",
    "EventEnvelope",
    "EventFanout",
    "EventHandler",
    "EventHandlerRegistry",
    "EventNotificationState",
    "EventPersister",
    "Every",
    "ExecutionConfiguration",
    "FixedDelay",
    "GenericState",
    "Id",
    "InProcessKeyedLockProvider",
    "JobContext",
    "JobRegistry",
    "KeyedLockProvider",
    "Lease",
    "LockAcquisitionError",
    "MariaDBKeyedLockProvider",
    "Model",
    "ModelBuilder",
    "ModelEvent",
    "ModelRepository",
    "MySQLKeyedLockProvider",
    "NoShardingStrategy",
    "OutboxRecord",
    "Persistable",
    "PostgresKeyedLockProvider",
    "Repository",
    "RetryConfig",
    "Schedule",
    "ShardConfig",
    "ShardIdentifier",
    "ShardedId",
    "ShardingConfig",
    "ShardingStrategy",
    "SingleTableJsonEventPersister",
    "StagedWork",
    "StaleRecordError",
    "StatementCount",
    "SystemClock",
    "TransactionManager",
    "TypedValue",
    "UnsupportedError",
    "ValidationError",
    "VirtualClock",
    "__version__",
    "count_statements",
    "current_clock",
    "current_plan",
    "current_principal",
    "emit_ddl",
    "migrate",
    "migrate_registry",
    "notifications_table",
    "outbox_table",
    "read_shard",
    "run_action",
    "scheduled_tasks_table",
    "sharded_uuid",
    "unwrap",
    "uuid_instant",
]
