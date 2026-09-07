# Java parity ledger

Every source file in `ekbatan` that belongs in `ekbatan-py`, and where it went.

The unit of work is **the file**, not a feature. A feature-driven port only finds what the porter
thought to look for, which is how the db-scheduler port shipped a schema MySQL refuses to create
and a task hand-off that had never worked. Reading each file whole, and porting it whole, makes
completeness something you can count rather than something you hope for.

**153 files, 23714 lines**: 88 main sources (13186 lines),
61 tests (10528 lines), 4 resources.

The tests are in the ledger deliberately. db-scheduler's ledger counted main sources only, and
the tests are where a contract is actually pinned - `ModelTest` is 513 lines of what a model may
and may not do, `ActionSpecTest` 633 lines of what the assertion helper promises. A port whose
main sources all read correctly can still diverge on every one of those, and nothing would say so.

Status is one of:

- **done** - the Java file has been read *in full*, and the Python side matches or diverges on
  purpose with the reason recorded
- **partial** - some of it is there; what is missing is named
- **todo** - not yet read whole. A Python file may well already exist; that is not the same thing
- **n/a** - deliberately not ported; the reason is given

A `todo` row with a Python file beside it means "something is there, and nobody has checked it
against the Java". That is the honest starting position for most of this table. The Python column
is a best guess at the destination, not a claim about what is in it.

## Out of scope

Excluded by decision, not by oversight:

| what | files | why |
| --- | --- | --- |
| `ekbatan-di` | 23 main, 14 test | Spring / Quarkus / Micronaut wiring. Revisit if and when a Python DI framework is worth targeting |
| `ekbatan-native` | 4 | GraalVM native-image configuration. CPython has no counterpart |
| `ekbatan-events/streaming/debezium-smt` | 7 | a Kafka Connect single-message transform - a Java artifact that runs inside Kafka Connect, not inside the application |
| `ekbatan-examples` | 755 main, 65 test | the same wallet application repeated across DI framework x build tool x database. `examples/wallet_service` is the Python counterpart |
| `ekbatan-integration-tests` | 40 main, 54 test | the Debezium/Kafka pipeline harness, which tests the SMT above |
| generated Avro/Protobuf Java | 4 | build output, not source |

Of the repository's 1154 `.java` files, that leaves the 149 below.

## Main sources

#### `core/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Action.java | 104 | `action/action.py` | todo |
| ActionExecutor.java | 516 | `action/executor.py` | todo |
| ActionPlan.java | 140 | `action/plan.py` | todo |
| ActionRegistry.java | 91 | `action/registry.py` | todo |
| ExecutionConfiguration.java | 107 | `action/action.py` | todo |
| Retry.java | 89 | `action/executor.py` | todo |
| RetryConfig.java | 37 | `action/action.py` | todo |

#### `core/action/persister`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ChangePersister.java | 148 | `action/persister.py` | todo |
| PersistableChanges.java | 89 | `action/plan.py` | todo |

#### `core/action/persister/event`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventPersister.java | 49 | `action/outbox.py` | todo |

#### `core/action/persister/event/single_table_json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventEntity.java | 308 | `action/outbox.py` | todo |
| EventEntityRepository.java | 238 | `action/outbox.py` | todo |
| SingleTableJsonEventPersister.java | 167 | `action/outbox.py` | todo |

#### `core/concurrent`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| KeyedLockProvider.java | 152 | `locks/base.py` | todo |
| KeyedReentrantHolder.java | 275 | `locks/_holder.py` | todo |
| LockAcquisitionException.java | 44 | `errors.py` | todo |
| MariaDBKeyedLockProvider.java | 257 | `locks/sql.py` | todo |
| MySQLKeyedLockProvider.java | 262 | `locks/sql.py` | todo |
| PostgresKeyedLockProvider.java | 267 | `locks/sql.py` | todo |

#### `core/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| DataSourceConfig.java | 238 | `config.py` | todo |
| PropertyKeyNormalizer.java | 49 | `config.py` | todo |
| ShardGroupConfig.java | 100 | `config.py` | todo |
| ShardMemberConfig.java | 183 | `config.py` | todo |
| ShardingConfig.java | 91 | `config.py` | todo |

#### `core/domain`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Entity.java | 193 | `domain.py` | todo |
| GenericState.java | 18 | `domain.py` | todo |
| Id.java | 77 | `ids.py` | todo |
| Identifiable.java | 16 | `domain.py` | todo |
| Model.java | 290 | `domain.py` | todo |
| ModelEvent.java | 42 | `domain.py` | todo |
| ModelId.java | 26 | `ids.py` | todo |
| Persistable.java | 35 | `domain.py` | todo |
| ShardedId.java | 98 | `ids.py` | todo |
| TypedValue.java | 50 | `domain.py` | todo |

#### `core/internal`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| CollectionUtils.java | 18 | - | todo |
| LockKeyHash.java | 48 | `locks/sql.py` | todo |
| MapUtils.java | 18 | - | todo |
| Validate.java | 149 | `errors.py` | todo |

#### `core/nativeimage`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Target_org_jooq_impl_Internal.java | 41 | - | n/a - GraalVM native-image substitution for jOOQ |

#### `core/persistence`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ConnectionProvider.java | 128 | `persistence.py` | todo |
| Transaction.java | 98 | `persistence.py` | todo |
| TransactionManager.java | 267 | `persistence.py` | todo |

#### `core/persistence/jooq/converter`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| InstantConverter.java | 49 | - | n/a - jOOQ type converter; SQLAlchemy binds types itself |
| JSONArrayNodeConverter.java | 53 | - | n/a - as above |
| JSONBArrayNodeConverter.java | 53 | - | n/a - as above |
| JSONBObjectNodeConverter.java | 56 | - | n/a - as above |
| JSONObjectNodeConverter.java | 57 | - | n/a - as above |

#### `core/persistence/jooq/converter/mysql`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| UuidBinaryConverter.java | 55 | - | n/a - as above |
| UuidStringConverter.java | 45 | - | n/a - as above |

#### `core/processor`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AutoBuilder.java | 18 | - | n/a - annotation processing; Python has no compile step |
| AutoBuilderProcessor.java | 262 | - | n/a - as above |

#### `core/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AbstractRepository.java | 939 | `repository.py` | todo |
| EntityRepository.java | 63 | `repository.py` | todo |
| ModelRepository.java | 83 | `repository.py` | todo |
| Repository.java | 93 | `repository.py` | todo |
| RepositoryRegistry.java | 124 | `repository.py` | todo |

#### `core/repository/exception`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EntityNotFoundException.java | 19 | `errors.py` | todo |
| StaleRecordException.java | 46 | `errors.py` | todo |

#### `core/shard`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| CrossShardException.java | 34 | `errors.py` | todo |
| DatabaseRegistry.java | 236 | `persistence.py` | todo |
| EmbeddedBitsShardingStrategy.java | 54 | `sharding.py` | todo |
| NoShardingStrategy.java | 32 | `sharding.py` | todo |
| ShardAwareId.java | 18 | `ids.py` | todo |
| ShardIdentifier.java | 77 | `sharding.py` | todo |
| ShardedUUID.java | 114 | `ids.py` | todo |
| ShardingStrategy.java | 45 | `sharding.py` | todo |

#### `distributedjobs`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| DistributedJob.java | 92 | `distributedjobs/__init__.py` | done |
| JobRegistry.java | 447 | `distributedjobs/__init__.py` | done |

#### `distributedjobs/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| JobsConfig.java | 141 | `distributedjobs/config.py` | done |

#### `events/localeventhandler`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventEnvelope.java | 227 | `events/handler.py` | todo |
| EventHandler.java | 62 | `events/handler.py` | todo |
| EventHandlerRegistry.java | 159 | `events/handler.py` | todo |

#### `events/localeventhandler/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| LocalEventHandlerConfig.java | 211 | `events/config.py` | todo |

#### `events/localeventhandler/job`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Backoff.java | 49 | `events/dispatch.py` | todo |
| EventFanoutJob.java | 282 | `events/jobs.py`, `events/fanout.py` | todo |
| EventHandlingJob.java | 596 | `events/jobs.py`, `events/dispatch.py` | todo |

#### `events/localeventhandler/model`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventNotification.java | 366 | `events/notification.py` | todo |
| EventNotificationState.java | 30 | `events/notification.py` | todo |

#### `events/localeventhandler/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventEntityRepository.java | 208 | `events/fanout.py` | todo |
| EventNotificationRepository.java | 376 | `events/notification.py` | todo |

#### `events/streaming/actionevent/json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionEvent.java | 301 | `_json.py` | todo |

#### `flyway`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| FlywayMigrator.java | 371 | `migrations.py` | todo |
| NativeImageFlywayResourceProvider.java | 92 | - | n/a - GraalVM native-image resource provider |

#### `keyedlock/redis`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| RedisKeyedLockProvider.java | 202 | - | todo |

#### `testsupport/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionAssert.java | 439 | `testing.py` | todo |
| ActionSpec.java | 116 | `testing.py` | todo |

#### `testsupport/testcontainers`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ClasspathTransferable.java | 39 | - | n/a - Testcontainers-Java file transfer; the Python client has its own |

#### `testsupport/time`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| VirtualClock.java | 142 | `clock.py` | todo |

## Tests

#### `core/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionExecutorNamespaceValidationTest.java | 119 | `tests/integration/test_registries.py` | todo |
| ActionExecutorTest.java | 519 | `tests/integration/test_actions.py` | todo |
| ActionExecutorTracingTest.java | 714 | `tests/integration/test_actions.py` | todo |
| ActionPlanTest.java | 223 | `tests/integration/test_actions.py` | todo |
| ActionRegistryBuilderTest.java | 61 | `tests/integration/test_registries.py` | todo |
| ActionRegistryTest.java | 112 | `tests/integration/test_registries.py` | todo |
| ActionSpecTest.java | 633 | `tests/unit/test_action_testing.py` | todo |
| CreateAction.java | 17 | `tests/fixtures/actions.py` | todo |
| CreateBothAction.java | 18 | `tests/fixtures/actions.py` | todo |
| DeleteAction.java | 19 | `tests/fixtures/actions.py` | todo |
| ExecutionConfigurationTest.java | 85 | `tests/integration/test_actions.py` | todo |
| FailingAction.java | 16 | `tests/fixtures/actions.py` | todo |
| RenameAction.java | 19 | `tests/fixtures/actions.py` | todo |
| RetryConfigTest.java | 55 | `tests/unit/test_policies.py` | todo |
| RetryTest.java | 292 | `tests/unit/test_policies.py` | todo |
| TestModel.java | 75 | `tests/fixtures/domain.py` | todo |
| TestProduct.java | 58 | `tests/fixtures/domain.py` | todo |

#### `core/action/persister`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ChangePersisterTest.java | 435 | `tests/integration/test_change_persister.py` | todo |
| PersistableChangesTest.java | 182 | `tests/integration/test_change_persister.py` | todo |

#### `core/action/persister/event/single_table_json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventEntityDeliveredDefaultTest.java | 65 | `tests/integration/test_outbox.py` | todo |

#### `core/concurrent`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| KeyedReentrantHolderTest.java | 94 | `tests/unit/test_locks.py` | todo |
| MariaDBKeyedLockProviderTest.java | 559 | `tests/integration/test_locks.py` | todo |
| MySQLKeyedLockProviderTest.java | 550 | `tests/integration/test_locks.py` | todo |
| PostgresKeyedLockProviderTest.java | 543 | `tests/integration/test_locks.py` | todo |

#### `core/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| DataSourceConfigDialectTest.java | 69 | `tests/unit/test_config.py` | todo |
| DataSourceConfigValidationTest.java | 106 | `tests/unit/test_config.py` | todo |
| PropertyKeyNormalizerTest.java | 72 | `tests/unit/test_config.py` | todo |
| ShardingConfigJacksonBindingTest.java | 170 | `tests/unit/test_config.py` | todo |
| ShardingConfigYamlTest.java | 236 | `tests/unit/test_config.py` | todo |

#### `core/domain`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EntityTest.java | 280 | `tests/unit/test_domain.py` | todo |
| IdTest.java | 159 | `tests/unit/test_ids.py` | todo |
| ModelEventTest.java | 89 | `tests/unit/test_domain.py` | todo |
| ModelTest.java | 513 | `tests/unit/test_domain.py` | todo |
| TypedValueTest.java | 112 | `tests/unit/test_domain.py` | todo |

#### `core/internal`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| CollectionUtilsTest.java | 27 | - | todo |
| LockKeyHashTest.java | 62 | `tests/unit/test_locks.py` | todo |
| MapUtilsTest.java | 24 | - | todo |
| ValidateTest.java | 164 | `tests/unit/test_errors.py` | todo |

#### `core/persistence`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| TransactionManagerTest.java | 280 | `tests/integration/test_persistence.py` | todo |

#### `core/persistence/jooq/converter`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| InstantConverterTest.java | 72 | - | n/a - tests a jOOQ converter that is not ported |
| JSONArrayNodeConverterTest.java | 83 | - | n/a - as above |
| JSONBArrayNodeConverterTest.java | 83 | - | n/a - as above |
| JSONBObjectNodeConverterTest.java | 83 | - | n/a - as above |
| JSONObjectNodeConverterTest.java | 83 | - | n/a - as above |

#### `core/persistence/jooq/converter/mysql`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| UuidBinaryConverterTest.java | 75 | - | n/a - as above |
| UuidStringConverterTest.java | 62 | - | n/a - as above |

#### `core/processor`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AutoBuilderProcessorTest.java | 165 | - | n/a - tests the annotation processor |

#### `core/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| RepositoryRegistryTest.java | 157 | `tests/integration/test_registries.py` | todo |

#### `core/shard`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| DatabaseRegistryFromConfigTest.java | 210 | `tests/integration/test_routing.py` | todo |
| DatabaseRegistrySecondaryPoolTest.java | 176 | `tests/integration/test_routing.py` | todo |
| ShardIdentifierTest.java | 62 | `tests/unit/test_ids.py` | todo |

#### `distributedjobs`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| JobRegistryBuilderTest.java | 204 | `tests/integration/test_jobs.py` | todo |
| JobRegistryDialectPinningTest.java | 123 | `tests/integration/test_jobs.py` | todo |

#### `distributedjobs/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| JobsConfigDurationValidationTest.java | 94 | `tests/unit/test_jobs_config.py` | todo |
| JobsConfigJacksonBindingTest.java | 142 | `tests/unit/test_jobs_config.py` | todo |

#### `events/localeventhandler`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventHandlerRegistryBuilderTest.java | 164 | `tests/integration/test_events.py` | todo |

#### `events/localeventhandler/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| LocalEventHandlerConfigJacksonBindingTest.java | 116 | `tests/unit/test_config.py` | todo |

#### `events/streaming/actionevent/json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionEventTest.java | 188 | `tests/unit/test_json.py` | todo |

#### `flyway`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| FlywayMigratorTest.java | 145 | `tests/integration/test_migrations.py` | todo |

#### `keyedlock/redis`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| RedisKeyedLockProviderLeaseTest.java | 41 | - | todo |

#### `testsupport/time`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| VirtualClockTest.java | 174 | `tests/unit/test_clock.py` | todo |

## Resources

| file | lines | Python | status |
| --- | --- | --- | --- |
| core/resources/META-INF/native-image/.../reachability-metadata.json | 0 | - | n/a - GraalVM reachability metadata |
| annotation-processor/resources/META-INF/services/javax.annotation.processing.Processor | 1 | - | n/a - Java service registration |
| events/streaming/action-event/avro/ActionEvent.avsc | 0 | - | n/a - Avro schema for the Kafka streaming path |
| events/streaming/action-event/protobuf/ActionEvent.proto | 0 | - | n/a - Protobuf schema, as above |
