# Java parity ledger

Every source file in `ekbatan` that belongs in `ekbatan-py`, and where it went.

The unit of work is **the file**, not a feature. A feature-driven port only finds what the porter
thought to look for, which is how the db-scheduler port shipped a schema MySQL refuses to create
and a task hand-off that had never worked. Reading each file whole, and porting it whole, makes
completeness something you can count rather than something you hope for.

**247 files, 33,208 lines**: 88 main sources (13,186), 61 unit tests (10,528),
94 integration tests (9,493), 4 resources.

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
| `ekbatan-integration-tests/event-pipeline` | 34 | the Debezium/Kafka harness, which tests the SMT above |
| `ekbatan-integration-tests/di` | 6 | Spring / Quarkus / Micronaut wiring, as above |
| generated Avro/Protobuf Java | 4 | build output, not source |

The rest of `ekbatan-integration-tests` is **in scope** and is listed below. An earlier version
of this table excluded the module wholesale as "the Kafka harness", which was wrong: it also
holds the per-database repository, persister, lock-provider and event-handler suites - 94 files
and 9,493 lines, including the two largest behavioural tests in the repository.

Of the repository's 1154 `.java` files, that leaves the 149 below.

## Noticed in the Java while porting

Not defects in this port, and not acted on here - recorded so they are not lost.

| where | what | impact |
| --- | --- | --- |
| `MySQLKeyedLockProvider`'s javadoc | says a sub-second `maxWait` "rounds up to the next whole second internally (verified)". Measured against MySQL 8.4.11, holding the lock and timing a second session: 0.25s and 0.5s both return in **0.00s**, while 1.5s waits 2.01s. So it rounds up above a second and truncates to zero below one - a 500ms wait is a poll, not a wait. MariaDB 11.4 honours the fraction exactly (0.26s, 0.51s, 1.51s) | documentation, not behaviour: both ports pass the value through unchanged, so they agree. What differs is what the comment leads a reader to expect |
| `EmbeddedBitsShardingStrategy.resolveShardIdentifier(persistable)` | the `ShardedId` branch decodes shard bits with no `version() != 7` guard, while the `Id` branch beside it has one. `ShardedUUID.from(uuid)` is public and documented as wrapping an arbitrary uuid "without re-checking that the shard bits are valid", so a pre-sharding version 4 id decodes to a well-formed, meaningless coordinate - verified by running: `ShardIdentifier(81, 21)`, `(202, 36)`, no exception | latent. `effectiveShard` falls back to the default for an unregistered coordinate, so it only bites when the garbage lands on a registered shard - about 8 in 16,384 per id on an eight-shard layout. Silent and self-consistent when it does: the row is written to the wrong database and read back from it |

This port refuses the same input - `ShardedId.shard` raises, and the strategy returns `None`,
which means "use the default".

## Main sources

#### `core/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Action.java | 104 | `action/action.py` | done - the plan, principal and clock are properties reading the executor's context |
| ActionExecutor.java | 516 | `action/executor.py` | done |
| ActionPlan.java | 140 | `action/plan.py` | done |
| ActionRegistry.java | 91 | `action/registry.py` | done |
| ExecutionConfiguration.java | 107 | `action/action.py` | done - `executor.py`; retry policies are an ordered tuple, not a map keyed by class |
| Retry.java | 89 | `action/executor.py` | done - folded into the executor's loop, as there is one caller |
| RetryConfig.java | 37 | `action/action.py` | done |

#### `core/action/persister`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ChangePersister.java | 148 | `action/persister.py` | done |
| PersistableChanges.java | 89 | `action/plan.py` | done |

#### `core/action/persister/event`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventPersister.java | 49 | `action/outbox.py` | done |

#### `core/action/persister/event/single_table_json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventEntity.java | 308 | `action/outbox.py` | done - rows are dicts through SQLAlchemy Core, not a mapped class |
| EventEntityRepository.java | 238 | `action/outbox.py` | n/a - package-private jOOQ field typing plus insert/read helpers; SQLAlchemy Core writes the row and `schema.py` declares the column types once |
| SingleTableJsonEventPersister.java | 167 | `action/outbox.py` | done |

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
| DataSourceConfig.java | 238 | `config.py` | done - credentials live in the url, as SQLAlchemy carries them |
| PropertyKeyNormalizer.java | 49 | `config.py` | n/a - nothing binds configuration from a property file here |
| ShardGroupConfig.java | 100 | `config.py` | done - folded into `ShardConfig`; one flat shard list, no group nesting |
| ShardMemberConfig.java | 183 | `config.py` | done - `ShardConfig`; the named `configs` map is deliberately not ported |
| ShardingConfig.java | 91 | `config.py` | done |

#### `core/domain`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Entity.java | 193 | `domain.py` | done |
| GenericState.java | 18 | `domain.py` | done |
| Id.java | 77 | `ids.py` | done |
| Identifiable.java | 16 | `domain.py` | done - `Persistable.id`; a marker interface naming a shape Python reads off the object |
| Model.java | 290 | `domain.py` | done |
| ModelEvent.java | 42 | `domain.py` | done |
| ModelId.java | 26 | `ids.py` | done - `str(id)` is `stringValue()` |
| Persistable.java | 35 | `domain.py` | done |
| ShardedId.java | 98 | `ids.py` | done |
| TypedValue.java | 50 | `domain.py` | done |

#### `core/internal`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| CollectionUtils.java | 18 | - | n/a - `if not collection` is the whole of it |
| LockKeyHash.java | 48 | `locks/base.py` | done - `locks/base.py`; the value is pinned against Java's own output |
| MapUtils.java | 18 | - | n/a - as above |
| Validate.java | 149 | `errors.py` | done - `errors.py` plus each type's own `__post_init__` |

#### `core/nativeimage`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Target_org_jooq_impl_Internal.java | 41 | - | n/a - GraalVM native-image substitution for jOOQ |

#### `core/persistence`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ConnectionProvider.java | 128 | `persistence.py` | n/a - a SQLAlchemy `Engine` is pool, dialect and lifecycle in one |
| Transaction.java | 98 | `persistence.py` | done - `persistence.py`; package-private in Java, and the autocommit dance is SQLAlchemy's |
| TransactionManager.java | 267 | `persistence.py` | done |

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
| AbstractRepository.java | 939 | `repository.py` | done - `Repository`, concrete rather than abstract |
| EntityRepository.java | 63 | `repository.py` | done |
| ModelRepository.java | 83 | `repository.py` | done |
| Repository.java | 93 | `repository.py` | n/a - the narrow interface has no second implementation here |
| RepositoryRegistry.java | 124 | `repository.py` | done - refuses a duplicate registration, and raises rather than returning null |

#### `core/repository/exception`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EntityNotFoundException.java | 19 | `errors.py` | done - `EntityNotFoundError` |
| StaleRecordException.java | 46 | `errors.py` | done - `StaleRecordError` |

#### `core/shard`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| CrossShardException.java | 34 | `errors.py` | done - `CrossShardError`, with Java's two named shards |
| DatabaseRegistry.java | 236 | `persistence.py` | done - `persistence.py`; jOOQ `DSLContext` maps have no counterpart |
| EmbeddedBitsShardingStrategy.java | 54 | `sharding.py` | done |
| NoShardingStrategy.java | 32 | `sharding.py` | done |
| ShardAwareId.java | 18 | `ids.py` | done - `Id.shard`; an interface naming a shape Python reads off the object |
| ShardIdentifier.java | 77 | `sharding.py` | done |
| ShardedUUID.java | 114 | `ids.py` | done - `sharded_uuid()`, `read_shard()`, `uuid_instant()` |
| ShardingStrategy.java | 45 | `sharding.py` | done |

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
| EventEnvelope.java | 227 | `events/handler.py` | done |
| EventHandler.java | 62 | `events/handler.py` | done - a Protocol; `aliases` included |
| EventHandlerRegistry.java | 159 | `events/handler.py` | done |

#### `events/localeventhandler/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| LocalEventHandlerConfig.java | 211 | `events/config.py` | done - `events/config.py` |

#### `events/localeventhandler/job`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Backoff.java | 49 | `events/dispatch.py` | done - `notification.py`; 30s base, doubling, capped, same clamp |
| EventFanoutJob.java | 282 | `events/jobs.py`, `events/fanout.py` | done |
| EventHandlingJob.java | 596 | `events/jobs.py`, `events/dispatch.py` | done - sequential rather than virtual-thread parallel; see DIVERGENCE |

#### `events/localeventhandler/model`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventNotification.java | 366 | `events/notification.py` | done - a row read through SQLAlchemy Core, not a mapped class |
| EventNotificationState.java | 30 | `events/notification.py` | done |

#### `events/localeventhandler/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventEntityRepository.java | 208 | `events/fanout.py` | n/a - package-private jOOQ field typing plus insert/read helpers; SQLAlchemy Core writes the row and `schema.py` declares the column types once |
| EventNotificationRepository.java | 376 | `events/notification.py` | done - the queries live in `dispatch.py` and `fanout.py` |

#### `events/streaming/actionevent/json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionEvent.java | 301 | `_json.py` | todo |

#### `flyway`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| FlywayMigrator.java | 371 | `migrations.py` | done - `migrations.py` drives Alembic per shard; see DIVERGENCE |
| NativeImageFlywayResourceProvider.java | 92 | - | n/a - GraalVM native-image resource provider |

#### `keyedlock/redis`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| RedisKeyedLockProvider.java | 202 | `locks/redis.py` | done - `locks/redis.py`, on redis-py |

#### `testsupport/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionAssert.java | 439 | `testing.py` | done - `StagedWork`; data accessors rather than 24 assertion methods |
| ActionSpec.java | 116 | `testing.py` | done - `run_action`; `assertThrows` is `pytest.raises` |

#### `testsupport/testcontainers`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ClasspathTransferable.java | 39 | - | n/a - Testcontainers-Java file transfer; the Python client has its own |

#### `testsupport/time`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| VirtualClock.java | 142 | `clock.py` | done - three modes, as Java has; `VirtualClock(instant)` additionally starts frozen |

## Tests

#### `core/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionExecutorNamespaceValidationTest.java | 119 | `tests/integration/test_registries.py` | done |
| ActionExecutorTest.java | 519 | `tests/integration/test_actions.py` | done |
| ActionExecutorTracingTest.java | 714 | `tests/integration/test_actions.py` | done |
| ActionPlanTest.java | 223 | `tests/integration/test_actions.py` | done - `add` hands back the stamped row rather than the argument; see DIVERGENCE |
| ActionRegistryBuilderTest.java | 61 | `tests/integration/test_registries.py` | done |
| ActionRegistryTest.java | 112 | `tests/integration/test_registries.py` | done |
| ActionSpecTest.java | 633 | `tests/unit/test_action_testing.py` | done - the cases testing the assertion DSL itself are n/a; `assert` needs no test |
| CreateAction.java | 17 | `tests/fixtures/actions.py` | todo |
| CreateBothAction.java | 18 | `tests/fixtures/actions.py` | todo |
| DeleteAction.java | 19 | `tests/fixtures/actions.py` | todo |
| ExecutionConfigurationTest.java | 85 | `tests/integration/test_actions.py` | done - the four map-shaped cases are n/a; policies are an ordered tuple here |
| FailingAction.java | 16 | `tests/fixtures/actions.py` | todo |
| RenameAction.java | 19 | `tests/fixtures/actions.py` | todo |
| RetryConfigTest.java | 55 | `tests/unit/test_policies.py` | done |
| RetryTest.java | 292 | `tests/unit/test_policies.py` | done - the interrupt-flag case is n/a; `time.sleep` raises rather than setting a flag |
| TestModel.java | 75 | `tests/fixtures/domain.py` | todo |
| TestProduct.java | 58 | `tests/fixtures/domain.py` | todo |

#### `core/action/persister`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ChangePersisterTest.java | 435 | `tests/integration/test_change_persister.py` | done |
| PersistableChangesTest.java | 182 | `tests/integration/test_change_persister.py` | done - the unmodifiable-map cases are n/a; reads return copies |

#### `core/action/persister/event/single_table_json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventEntityDeliveredDefaultTest.java | 65 | `tests/integration/test_outbox.py` | todo |

#### `core/concurrent`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| KeyedReentrantHolderTest.java | 94 | `tests/unit/test_locks.py` | done |
| MariaDBKeyedLockProviderTest.java | 559 | `tests/integration/test_locks.py` | n/a - as above |
| MySQLKeyedLockProviderTest.java | 550 | `tests/integration/test_locks.py` | n/a - the same suite; Python parametrises rather than subclassing |
| PostgresKeyedLockProviderTest.java | 543 | `tests/integration/test_locks.py` | done - `tests/unit/test_locks.py` plus the integration suite; the Mockito cases are n/a |

#### `core/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| DataSourceConfigDialectTest.java | 69 | `tests/unit/test_config.py` | done |
| DataSourceConfigValidationTest.java | 106 | `tests/unit/test_config.py` | done |
| PropertyKeyNormalizerTest.java | 72 | `tests/unit/test_config.py` | n/a - tests a normaliser that is not ported |
| ShardingConfigJacksonBindingTest.java | 170 | `tests/unit/test_config.py` | n/a - Jackson binding |
| ShardingConfigYamlTest.java | 236 | `tests/unit/test_config.py` | n/a - YAML binding, as above |

#### `core/domain`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EntityTest.java | 280 | `tests/unit/test_domain.py` | done |
| IdTest.java | 159 | `tests/unit/test_ids.py` | done |
| ModelEventTest.java | 89 | `tests/unit/test_domain.py` | done |
| ModelTest.java | 513 | `tests/unit/test_domain.py` | done |
| TypedValueTest.java | 112 | `tests/unit/test_domain.py` | done |

#### `core/internal`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| CollectionUtilsTest.java | 27 | - | n/a - tests a helper that is not ported |
| LockKeyHashTest.java | 62 | `tests/unit/test_locks.py` | done |
| MapUtilsTest.java | 24 | - | n/a - as above |
| ValidateTest.java | 164 | `tests/unit/test_errors.py` | todo |

#### `core/persistence`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| TransactionManagerTest.java | 280 | `tests/integration/test_persistence.py` | done - the seven autocommit cases are SQLAlchemy's to own |

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
| RepositoryRegistryTest.java | 157 | `tests/integration/test_registries.py` | done |

#### `core/shard`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| DatabaseRegistryFromConfigTest.java | 210 | `tests/integration/test_routing.py` | todo |
| DatabaseRegistrySecondaryPoolTest.java | 176 | `tests/integration/test_routing.py` | todo |
| ShardIdentifierTest.java | 62 | `tests/unit/test_ids.py` | done |

#### `distributedjobs`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| JobRegistryBuilderTest.java | 204 | `tests/integration/test_jobs.py` | todo |
| JobRegistryDialectPinningTest.java | 123 | `tests/integration/test_jobs.py` | todo |

#### `distributedjobs/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| JobsConfigDurationValidationTest.java | 94 | `tests/unit/test_jobs_config.py` | todo |
| JobsConfigJacksonBindingTest.java | 142 | `tests/unit/test_jobs_config.py` | n/a - Jackson binding |

#### `events/localeventhandler`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| EventHandlerRegistryBuilderTest.java | 164 | `tests/integration/test_events.py` | todo |

#### `events/localeventhandler/config`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| LocalEventHandlerConfigJacksonBindingTest.java | 116 | `tests/unit/test_config.py` | n/a - Jackson binding |

#### `events/streaming/actionevent/json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ActionEventTest.java | 188 | `tests/unit/test_json.py` | todo |

#### `flyway`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| FlywayMigratorTest.java | 145 | `tests/integration/test_migrations.py` | done - the four native-image cases are n/a |

#### `keyedlock/redis`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| RedisKeyedLockProviderLeaseTest.java | 41 | `tests/integration/test_redis_locks.py` | done |

#### `testsupport/time`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| VirtualClockTest.java | 174 | `tests/unit/test_clock.py` | done - `getZone`/`withZone` are n/a; the protocol here asks only for `now()` |

## Integration tests

Java runs the behavioural suites against every supported database by subclassing one base
class per dialect - `BaseRepositoryTest` (1251 lines) has `Pg`, `Mysql` and `Mariadb` subclasses
that do nothing but supply a container. Python gets the same coverage from one test run under
`--dialects=all` (see `tests/backends.py`), so the base classes are the work and the per-dialect
subclasses are marked n/a with that reason.

**The default is `--dialects=sqlite`.** A green run proves SQLite and nothing else; `all` is what
proves the three real servers.

#### `core/action/persister/event/single_table_json`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| BaseSingleTableJsonEventPersisterTest.java | 419 | `tests/integration/test_outbox.py` | todo |
| MariadbSingleTableJsonEventPersisterTest.java | 51 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| MysqlSingleTableJsonEventPersisterTest.java | 51 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| PgSingleTableJsonEventPersisterTest.java | 48 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `core/test/event`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| TestActionParams.java | 3 | `tests/fixtures/` | todo |
| TestModelEvent.java | 68 | `tests/fixtures/` | todo |
| TestStatusChangedEvent.java | 29 | `tests/fixtures/` | todo |

#### `core/test/model`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Aliases.java | 50 | `tests/fixtures/` | todo |
| Dummy.java | 105 | `tests/fixtures/` | todo |
| DummyState.java | 7 | `tests/fixtures/` | todo |
| SomeTest.java | 11 | `tests/fixtures/` | todo |

#### `core/test/model/events`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| DummyCreatedEvent.java | 21 | `tests/fixtures/` | todo |
| DummyDeletedEvent.java | 12 | `tests/fixtures/` | todo |
| DummyMoneyDepositedEvent.java | 17 | `tests/fixtures/` | todo |
| DummyMoneyWithdrawnEvent.java | 17 | `tests/fixtures/` | todo |

#### `core/test/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| BaseRepositoryTest.java | 1251 | `tests/integration/test_repository.py` | done - `tests/integration/test_repository.py` |
| BaseShardedRepositoryTest.java | 676 | `tests/integration/test_routing.py` | done - `tests/integration/test_routing.py` |
| DummyRepository.java | 52 | `tests/fixtures/schema.py` | todo |
| DummyRepository.java | 52 | `tests/fixtures/schema.py` | todo |
| DummyRepository.java | 52 | `tests/fixtures/schema.py` | todo |
| MariadbRepositoryTest.java | 56 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| MariadbShardedRepositoryTest.java | 80 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| MysqlRepositoryTest.java | 56 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| MysqlShardedRepositoryTest.java | 79 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| PgRepositoryTest.java | 117 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| PgShardedRepositoryTest.java | 80 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| ShardedDummyRepository.java | 53 | `tests/fixtures/schema.py` | todo |
| ShardedDummyRepository.java | 53 | `tests/fixtures/schema.py` | todo |
| ShardedDummyRepository.java | 53 | `tests/fixtures/schema.py` | todo |

#### `test/distributed_jobs_pg`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| JobRegistryIntegrationTest.java | 262 | `tests/integration/test_jobs.py` | todo |

#### `test/keyed_lock_provider`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| MariaDBKeyedLockProviderIntegrationTest.java | 502 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| MySQLKeyedLockProviderIntegrationTest.java | 500 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |
| PostgresKeyedLockProviderIntegrationTest.java | 470 | `tests/integration/test_locks.py` | done - `tests/integration/test_locks.py`, parametrised over the three servers |
| RedisKeyedLockProviderIntegrationTest.java | 622 | - | done - `tests/integration/test_redis_locks.py`; the three Redisson `CompletionException` cases are n/a |
| WalletDepositLockingIntegrationTest.java | 165 | `tests/integration/test_locks.py` | todo |

#### `test/keyed_lock_provider/wallet/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WalletCloseAction.java | 27 | `tests/fixtures/` | todo |
| WalletCreateAction.java | 31 | `tests/fixtures/` | todo |
| WalletDepositAction.java | 40 | `tests/fixtures/` | todo |

#### `test/keyed_lock_provider/wallet/models`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Wallet.java | 80 | `tests/fixtures/` | todo |
| WalletState.java | 6 | `tests/fixtures/` | todo |

#### `test/keyed_lock_provider/wallet/models/events`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WalletClosedEvent.java | 13 | `tests/fixtures/` | todo |
| WalletCreatedEvent.java | 21 | `tests/fixtures/` | todo |
| WalletMoneyDepositedEvent.java | 18 | `tests/fixtures/` | todo |

#### `test/keyed_lock_provider/wallet/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WalletRepository.java | 47 | `tests/fixtures/` | todo |

#### `test/local_event_handler`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| BaseLocalEventHandlerIntegrationTest.java | 604 | `tests/integration/test_events.py`, `test_event_jobs.py` | todo |

#### `test/local_event_handler/audit/models`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AuditEntry.java | 44 | `tests/fixtures/` | todo |

#### `test/local_event_handler/note/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| NoteCreateAction.java | 25 | `tests/fixtures/` | todo |

#### `test/local_event_handler/note/handler`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| NoteCreatedAuditEntryHandler.java | 42 | `tests/fixtures/` | todo |
| NoteCreatedAuditHandler.java | 35 | `tests/fixtures/` | todo |

#### `test/local_event_handler/note/models`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Note.java | 41 | `tests/fixtures/` | todo |
| NoteState.java | 6 | `tests/fixtures/` | todo |

#### `test/local_event_handler/note/models/events`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| NoteCreatedEvent.java | 30 | `tests/fixtures/` | todo |

#### `test/local_event_handler/widget/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WidgetCreateAction.java | 25 | `tests/fixtures/` | todo |

#### `test/local_event_handler/widget/handler`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AlwaysFailingWidgetCreatedHandler.java | 31 | `tests/fixtures/` | todo |
| ErrorThrowingWidgetCreatedHandler.java | 40 | `tests/fixtures/` | todo |
| FlakyWidgetCreatedHandler.java | 38 | `tests/fixtures/` | todo |
| SlowAlwaysFailingHandler.java | 45 | `tests/fixtures/` | todo |
| WidgetCreatedAutoNoteHandler.java | 48 | `tests/fixtures/` | todo |
| WidgetCreatedEmailHandler.java | 35 | `tests/fixtures/` | todo |
| WidgetCreatedIndexerHandler.java | 35 | `tests/fixtures/` | todo |

#### `test/local_event_handler/widget/models`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Widget.java | 41 | `tests/fixtures/` | todo |
| WidgetState.java | 6 | `tests/fixtures/` | todo |

#### `test/local_event_handler/widget/models/events`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WidgetCreatedEvent.java | 30 | `tests/fixtures/` | todo |

#### `test/local_event_handler_mariadb`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| MariadbLocalEventHandlerIntegrationTest.java | 96 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_mariadb/audit/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AuditEntryRepository.java | 45 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_mariadb/note/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| NoteRepository.java | 46 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_mariadb/widget/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WidgetRepository.java | 46 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_mysql`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| MysqlLocalEventHandlerIntegrationTest.java | 96 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_mysql/audit/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AuditEntryRepository.java | 45 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_mysql/note/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| NoteRepository.java | 46 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_mysql/widget/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WidgetRepository.java | 46 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_pg`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| PgLocalEventHandlerIntegrationTest.java | 122 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_pg/audit/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| AuditEntryRepository.java | 45 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_pg/note/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| NoteRepository.java | 46 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/local_event_handler_pg/widget/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WidgetRepository.java | 46 | - | n/a - the per-dialect subclass; Python parametrises one test with `--dialects` |

#### `test/postgres_sharded/wallet/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| ShardedWalletIntegrationTest.java | 383 | `tests/integration/test_routing.py` | todo |
| WalletCreateAction.java | 43 | `tests/fixtures/` | todo |
| WalletCreateMultiShardAction.java | 43 | `tests/fixtures/` | todo |
| WalletCreateThenFailMultiShardAction.java | 57 | `tests/fixtures/` | todo |

#### `test/postgres_sharded/wallet/models`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Wallet.java | 78 | `tests/fixtures/` | todo |
| WalletState.java | 7 | `tests/fixtures/` | todo |

#### `test/postgres_sharded/wallet/models/events`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WalletCreatedEvent.java | 21 | `tests/fixtures/` | todo |
| WalletMoneyDepositedEvent.java | 18 | `tests/fixtures/` | todo |

#### `test/postgres_sharded/wallet/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WalletRepository.java | 49 | `tests/fixtures/` | todo |

#### `test/postgres_simple/wallet/action`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| SimpleWalletIntegrationTest.java | 174 | `tests/integration/test_actions.py` | todo |
| WalletCloseAction.java | 27 | `tests/fixtures/` | todo |
| WalletCreateAction.java | 31 | `tests/fixtures/` | todo |
| WalletDepositAction.java | 28 | `tests/fixtures/` | todo |

#### `test/postgres_simple/wallet/models`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| Wallet.java | 80 | `tests/fixtures/` | todo |
| WalletState.java | 6 | `tests/fixtures/` | todo |

#### `test/postgres_simple/wallet/models/events`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WalletClosedEvent.java | 13 | `tests/fixtures/` | todo |
| WalletCreatedEvent.java | 21 | `tests/fixtures/` | todo |
| WalletMoneyDepositedEvent.java | 18 | `tests/fixtures/` | todo |

#### `test/postgres_simple/wallet/repository`

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| WalletRepository.java | 47 | `tests/fixtures/` | todo |

## Resources

| file | lines | Python | status |
| --- | --- | --- | --- |
| core/resources/META-INF/native-image/.../reachability-metadata.json | 0 | - | n/a - GraalVM reachability metadata |
| annotation-processor/resources/META-INF/services/javax.annotation.processing.Processor | 1 | - | n/a - Java service registration |
| events/streaming/action-event/avro/ActionEvent.avsc | 0 | - | n/a - Avro schema for the Kafka streaming path |
| events/streaming/action-event/protobuf/ActionEvent.proto | 0 | - | n/a - Protobuf schema, as above |
