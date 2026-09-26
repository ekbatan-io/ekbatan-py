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
    ActionExecutorBuilder,
    ActionPlan,
    ActionRegistry,
    ActionRegistryBuilder,
    ChangePersister,
    EventPersister,
    ExecutionConfiguration,
    ExecutionConfigurationBuilder,
    OutboxRecord,
    RetryConfig,
    SingleTableJsonEventPersister,
    current_plan,
)
from .clock import Clock, SystemClock, VirtualClock
from .config import (
    DataSourceConfig,
    DataSourceConfigBuilder,
    ShardGroupConfig,
    ShardGroupConfigBuilder,
    ShardingConfig,
    ShardingConfigBuilder,
    ShardMemberConfig,
    ShardMemberConfigBuilder,
)
from .distributedjobs import (
    DistributedJob,
    ExecutionContext,
    JobRegistry,
    JobRegistryBuilder,
    JobsConfig,
    Schedule,
    job_registry,
)
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
    EventFanoutJob,
    EventHandler,
    EventHandlerRegistry,
    EventHandlingJob,
    EventNotificationState,
    HandlingConfig,
    LocalEventHandlerConfig,
)
from .fetching import Db, FetchResult
from .ids import Id, ShardedId, TypedValue, unwrap
from .locks import (
    KeyedLockProvider,
    Lease,
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
)
from .migrations import emit_ddl, migrate, migrate_registry
from .persistence import (
    DatabaseRegistry,
    DatabaseRegistryBuilder,
    Transaction,
    TransactionManager,
    engine_for,
)
from .repository import (
    AbstractRepository,
    EntityRepository,
    ModelRepository,
    Repository,
    RepositoryRegistry,
    RepositoryRegistryBuilder,
)
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
from .testing import ActionAssert, ActionSpec, StagedWork, run_action

__version__ = "0.1.0.dev0"

__all__ = [
    "DEFAULT_RETRIES",
    "DEFAULT_SHARD",
    "AbstractRepository",
    "Action",
    "ActionAssert",
    "ActionExecutor",
    "ActionExecutorBuilder",
    "ActionPlan",
    "ActionRegistry",
    "ActionRegistryBuilder",
    "ActionSpec",
    "ChangePersister",
    "Clock",
    "ConnectivityError",
    "CrossShardError",
    "DataSourceConfig",
    "DataSourceConfigBuilder",
    "DatabaseRegistry",
    "DatabaseRegistryBuilder",
    "Db",
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
    "EventFanoutJob",
    "EventHandler",
    "EventHandlerRegistry",
    "EventHandlingJob",
    "EventNotificationState",
    "EventPersister",
    "ExecutionConfiguration",
    "ExecutionConfigurationBuilder",
    "ExecutionContext",
    "FetchResult",
    "GenericState",
    "HandlingConfig",
    "Id",
    "JobRegistry",
    "JobRegistryBuilder",
    "JobsConfig",
    "KeyedLockProvider",
    "Lease",
    "LocalEventHandlerConfig",
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
    "RepositoryRegistry",
    "RepositoryRegistryBuilder",
    "RetryConfig",
    "Schedule",
    "ShardGroupConfig",
    "ShardGroupConfigBuilder",
    "ShardIdentifier",
    "ShardMemberConfig",
    "ShardMemberConfigBuilder",
    "ShardedId",
    "ShardingConfig",
    "ShardingConfigBuilder",
    "ShardingStrategy",
    "SingleTableJsonEventPersister",
    "StagedWork",
    "StaleRecordError",
    "StatementCount",
    "SystemClock",
    "Transaction",
    "TransactionManager",
    "TypedValue",
    "UnsupportedError",
    "ValidationError",
    "VirtualClock",
    "__version__",
    "count_statements",
    "current_plan",
    "emit_ddl",
    "engine_for",
    "job_registry",
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
