# Dracula sweep - area A: domain and persistence

Files: `domain.py`, `ids.py`, `repository.py`, `persistence.py`, `fetching.py`, `schema.py`,
`sharding.py`, `clock.py`, `errors.py`, `accounting.py`, `_json.py`, and what `__init__.py`
exports from them. Compared with `ekbatan-core` (and `ekbatan-test-support` for the clock).

Paths: Python is relative to `ekbatan-py/src/ekbatan/`; Java is relative to
`ekbatan/ekbatan-core/src/main/java/io/ekbatan/core/` unless written in full.

Checked by running (2026-10-12, scratch scripts, SQLite file database): findings 1, 2, 3, 7,
12, 13, 16, 20, 27. The rest were checked by reading both sides.

---

### 1. ~~A model's timestamps become naive datetimes (no time zone)~~ fixed (`EKB-0294`)

> Superseded (`EKB-0384`): the framework no longer converts time at all. A model keeps its
> dates exactly as given, every read hands back what the driver returns, and
> `SystemClock().instant()` (`now()` until `EKB-0426`) is UTC with no zone attached - so the model, the column and the clock
> agree, and nothing compares naive with aware. See Part 1, bug 11 in `docs/DRACULAS.md`.

- **What:** Whatever `created_date` / `updated_date` you pass, the model keeps a datetime with
  the zone removed, moved to UTC. `12:00+02:00` goes in and `datetime(…, 10, 0)` with no zone
  comes out. `clock.now()` returns a datetime *with* a zone, so `wallet.created_date < clock.now()`
  raises `TypeError`, and `==` is always `False`. Java keeps an `Instant`, which always means a
  point in time.
- **Python:** `domain.py:654` (`to_storage(self.created_date)`), `clock.py:132-144` (`to_storage`
  drops `tzinfo`), `clock.py:22-23` (`now()` returns an aware datetime).
- **Java:** `domain/Model.java:84-86` (`Instant`, only `.truncatedTo(MICROS)`).
- **Why:** a choice - so a model holds exactly what the zone-less column gives back on a read.
- **Could match Java?** partly - keep the value aware in UTC in memory, and remove the zone only
  in `to_row` / the column type.
- **Surprise:** high

### 2. ~~The event payload uses Python field names, decimals as strings~~, and you can't change the JSON writer
- **Fixed** (`EKB-0290`): payloads and params are the text Java's default Jackson 3 mapper writes, byte for byte - `newBalance`, declaration order, decimals as JSON numbers. A pluggable writer is still not offered.
- **What:** `WalletMoneyDeposited(new_balance=…)` is stored as
  `{"amount":"10.50","modelId":"abc","modelName":"Wallet","new_balance":"20.00"}`. Only
  `modelId`/`modelName` are camelCase; every other key is snake_case. Decimals are strings and keys
  are sorted. Java writes the payload with the `ObjectMapper` the application hands in, so keys are
  the Java field names (`newBalance`, `initialBalance`) and a `BigDecimal` is a JSON number by
  default. So a Java consumer reading a Python-written payload (or the other way round) does not
  find its fields. There is no hook to plug in another writer.
- **Python:** `domain.py:121-147` (`payload()` uses `dataclasses.fields`), `_json.py:28`
  (decimal as string), `_json.py:72` (sorted keys), `action/outbox.py:364`.
- **Java:** `action/persister/event/single_table_json/SingleTableJsonEventPersister.java:132`
  (`objectMapper.valueToTree(event)`); `ActionExecutor.java:450` (`objectMapper(...)` on the
  builder); example field names in
  `ekbatan-examples/spring-boot-wallet-rest-maven-native-mysql/.../events/WalletCreatedEvent.java:14-16`.
- **Why:** a Python convention (field names are snake_case) plus a choice (one fixed JSON form).
- **Could match Java?** partly - camelCase the keys in `payload()` and let the application pass its
  own serializer; decimal-as-number would need agreement with the Java side.
- **Surprise:** high

### 3. ~~Opening a transaction inside a transaction joins the outer one~~ fixed (`EKB-0301`)
- **Fixed:** every `transaction()` opens a new transaction on its own connection, as Java's
  `inTransaction` does; the docs carry Java's "do not nest" warning. The text below is what it was.
- **What:** `with tm.transaction():` inside another `transaction()` on the same shard hands back
  the *same* connection; nothing commits until the outer block ends. Java's `inTransaction`
  always takes a new connection and opens a second, separate transaction (which commits on its
  own).
- **Python:** `persistence.py:134-139` (`existing = self.current()` → yield it). Ran it: the inner
  `tx.connection is` the outer one.
- **Java:** `persistence/TransactionManager.java:166` (`ScopedValue.where(...)` on a freshly
  acquired connection every call).
- **Why:** a choice - so a repository call inside a unit of work always lands in the same
  transaction.
- **Could match Java?** yes - open a new connection every time; the joining path would then
  belong only to `tx_db_else_db`.
- **Surprise:** high

### 4. A model must name its builder as a type argument, and only finds out at runtime - settled by the user (2026-10-24)
- **Settled by the user (2026-10-24)**: kept as it is - the error at `builder()` or `copy()` says what to write and where. Refusing at class definition was weighed and left: the builder generator imports the model before the builder exists, and an entity that never calls `builder()` (the examples' `Alert`) needs none.
- **What:** You write `class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder])`. The
  type arguments are in a different order from Java (`Model<Wallet, ID, STATE>`): there is no
  self type, and the third one is the *builder*. Leave the builder out and the class still
  works, but `wallet.copy()` / `Wallet.builder()` raise `EkbatanError` at the moment you call them.
  Java's annotation processor generates the builder at compile time and every model writes
  `copy()`. Also: `Entity`'s state type defaults to `GenericState`, and the builder works out its
  setters by name at runtime (`__getattr__`), so an untyped `EntityBuilder(Wallet)` accepts any
  field name the model has.
- **Python:** `domain.py:453-458`, `domain.py:582-585` (`S: Enum = GenericState`),
  `domain.py:346-369` (`_declared_builder`), `domain.py:417-449` (`_no_typed_builder`),
  `domain.py:553-562`, `domain.py:257-269`. Ran it: `Wallet.copy()` with no builder raised
  `EkbatanError`.
- **Java:** `domain/Model.java:53` (`Model<MODEL extends Model<MODEL,ID,STATE>, ID, STATE>`),
  `domain/Model.java:107` (`abstract copy()`), `domain/Entity.java:30-31`.
- **Why:** Python forces it - there is no compile step, so the check happens when you use it.
- **Could match Java?** partly - the order of type arguments could follow Java, but the
  check can only ever happen at runtime (or in a type checker).
- **Surprise:** high

### 5. ~~The outbox table is not in an `eventlog` schema by default~~ matched (`EKB-0520`, `EKB-0526`)
- **Fixed** (`EKB-0520`, the user's decision 2026-10-24): the tables are fixed, as Java's, and nothing passes them - `eventlog.events` / `eventlog.event_notifications` on PostgreSQL; SQLite has no schemas. See `docs/EVENTLOG-LEDGER.md`.
- **Matched on MySQL and MariaDB too** (`EKB-0526`, the user's decision 2026-10-24, reversing option 2 there): `eventlog` is a database of its own, created by the first migration (`CREATE DATABASE IF NOT EXISTS eventlog;`, Java's `V0000__create_eventlog_database.sql`), one per server and shared by every database on it. See `docs/EVENTLOG-JAVA-WAY-LEDGER.md`.
- **What:** `outbox_table(metadata)` makes a table called `events` in the default schema (for
  example `public.events`), and the examples use it that way. Java always reads and writes
  `eventlog.events`. A Java developer will look for `eventlog.events` and not find it, and a Java
  and a Python service sharing one database write to different tables.
- **Python:** `schema.py:112` (`name="events", schema=None`); `action/outbox.py:223-226` (the
  table is handed in); `ekbatan-py-examples/fastapi_wallet_rest_mysql/schema.py:50`.
- **Java:** `action/persister/event/single_table_json/EventEntityRepository.java:35-38`
  (`SCHEMA_NAME = "eventlog"`).
- **Why:** a choice - SQLite has no schemas, so the application declares the table itself.
- **Could match Java?** yes - default `schema="eventlog"` on the dialects that have schemas
  (and a database named `eventlog` on MySQL/MariaDB, as Java uses).
- **Surprise:** high

### 6. A repository's key is the typed id, not the raw UUID
- **What:** Python repositories are `ModelRepository[Wallet, ShardedId[Wallet]]` and
  `find(wallet.id)` takes the `ShardedId`. Java repositories are
  `ModelRepository<Wallet, WalletsRecord, Wallets, UUID>` and `findById(UUID)` takes the raw
  column value (`wallet.id.getValue()`). Python also accepts a raw UUID, because it unwraps
  whatever it gets.
- **Python:** `repository.py:559` (`find(self, key: K)`),
  `ekbatan-py-examples/fastapi_wallet_rest_mysql/repository.py:37`.
- **Java:** `repository/AbstractRepository.java:688` (`findById(DB_ID id)`);
  `ekbatan-examples/spring-boot-wallet-rest-maven-native-mysql/.../WalletRepository.java:23`.
- **Why:** a choice - Python has no jOOQ record type to supply the column type, so the second
  type argument is used for the id the application holds.
- **Could match Java?** yes - document and type it as the raw value; it already works that way
  at runtime.
- **Surprise:** medium

### 7. ~~Built-in reads join an open transaction~~ fixed (`EKB-0259`)

> Now as Java: `_reader` goes through `db()`, outside any open transaction. Both ports run side
> by side on PostgreSQL: inside the transaction neither finds the row; after commit both do.

- **What:** `find`, `get`, `find_where`, `count` and so on run inside the transaction if one is
  open on that shard, so they see rows written in it but not yet committed. Java's `findById`,
  `findAll`, `countWhere`… use `db()`, a separate connection outside any transaction, so they don't.
  (`DIVERGENCE.md` "The connection accessors are Java's, exactly" says the built-in reads match
  Java; on this point they don't.)
- **Python:** `repository.py:393-404` (`_reader` → `TransactionManager.connection()`, which
  joins), `persistence.py:225-228`. Ran it: inside `transaction()`, `repo.find(id)` returned a
  row added there and not yet committed.
- **Java:** `repository/AbstractRepository.java:688-692` (`db(id).selectFrom(...)`),
  `repository/AbstractRepository.java:182-184` (`db()` = the plain primary context).
- **Why:** a choice - a read in a unit of work should see what the unit of work did.
- **Could match Java?** yes - use the plain primary connection (`db()`) in `_reader`.
- **Surprise:** medium

### 8. ~~The read methods have different names, not just snake_case~~ names matched (`EKB-0538`)
- **Matched** (`EKB-0538`, the user's decision 2026-10-25): the reads are Java's names in snake case - `find_by_id`, `get_by_id`, `find_all_by_ids`, `find_all`, `exists_by_id`, `count`, `count_where`, `find_one_where`, `find_all_where`, `exists_where` - on the sync repository and its `ekbatan.aio` twin; the old names are gone, with no alias. `count()` counts every live row and `count_where(...)` the matching ones, as Java's `count()` / `countWhere(c)`. A `*_where` read needs one condition, as Java's do - called with none it is refused - and still takes several, and `shard=`. A missing row stays `None`: Python's way of saying a value may be missing. See `docs/REPO-NAMES-LEDGER.md`.
- **What:** `find` / `get` / `find_many` / `find_where` / `find_one_where` / `all` / `exists` /
  `exists_where` / `count(*criteria)` stand for Java's `findById` / `getById` / `findAllByIds` /
  `findAllWhere` / `findOneWhere` / `findAll` / `existsById` / `existsWhere` / `count()` +
  `countWhere(c)`. `find` returns `None` where Java returns `Optional`. The `*_where` methods take
  any number of SQLAlchemy conditions and an optional `shard=`.
- **Python:** `repository.py:559, 566, 573, 611, 617, 651, 654, 659, 677`.
- **Java:** `repository/AbstractRepository.java:688, 701, 715, 751, 763, 772, 784, 798, 813, 826`.
- **Why:** a choice for the names; `None` instead of `Optional` is a Python convention.
- **Could match Java?** yes for the names (`find_by_id`, `get_by_id`, `find_all_by_ids`,
  `find_all_where`, `find_all`, `exists_by_id`, `count_where`); no for `Optional`.
- **Surprise:** medium

### 9. Tables and queries are SQLAlchemy, written by hand, and the row mapping is a dict
- **What:** There is no jOOQ and no generated table classes. The application writes a SQLAlchemy
  `Table(...)` per table, and `to_row` returns a `dict` in which it converts each value itself
  (`wallet.id.value`, `wallet.state.name`; times as they are), and `from_row` gets a `Row` *or* a
  plain dict. The constructor takes `(model_type, table, registry, sharding=, clock=,
  state_column=)`: no id field (it reads the table's single primary key and refuses a composite
  one), and the state column name can be changed. Two Python-only helpers,
  `columns_by_field_name` / `model_by_field_name`, map by field name if you ask for them.
- **Python:** `repository.py:207-216`, `repository.py:274-323`;
  `ekbatan-py-examples/fastapi_wallet_rest_mysql/repository.py:49-86`.
- **Java:** `repository/AbstractRepository.java:113-142` (`domainClass, table, idField,
  databaseRegistry, shardingStrategy`), `:167` / `:175` (`fromRecord` / `toRecord` on generated
  records).
- **Why:** a choice - SQLAlchemy is the usual Python tool where Java uses jOOQ.
- **Could match Java?** partly - the shape (abstract `to_row`/`from_row`) already matches; the
  query library cannot.
- **Surprise:** medium

### 10. Connections come from `with` blocks, are public, and the "all shards" ones give pairs
- **What:** `db()`, `readonly_db()`, `tx_db_else_db()` are context managers (`with self.db() as
  conn:`), not methods returning a context you call straight away. They are public (Java's are
  `protected`), take keyword arguments `key=` / `model=` / `shard=` instead of overloads, and
  `dbs()` / `readonly_dbs()` lazily give `(shard, conn)` pairs instead of a `Collection`.
  `tx_db()` returns the connection or `None` instead of `Optional`.
- **Python:** `repository.py:357-391`, `repository.py:426-487`.
- **Java:** `repository/AbstractRepository.java:182-343`.
- **Why:** Python forces it partly (a connection has to be given back, which a `with` block does);
  the rest is a Python convention.
- **Could match Java?** partly - the `with` block has to stay; protected/public can't be enforced
  in Python anyway.
- **Surprise:** medium

### 11. `fetch_one` does not mean what jOOQ's `fetchOne` means
- **What:** On `conn.rows(stmt)`, `fetch_one` means *exactly one row, error if none or two*;
  jOOQ's `fetchOne` means *at most one* (null if none). jOOQ's `fetchAny` is `fetch_first` here, and
  jOOQ's `fetchOne` is `fetch_one_or_none`.
- **Python:** `fetching.py:51-54` (says so), `:68`, `:73`, `:81`.
- **Java:** none in ekbatan (jOOQ's `ResultQuery.fetchOne` / `fetchAny`, used at
  `repository/AbstractRepository.java:690`).
- **Why:** a choice - follows SQLAlchemy's `one()` / `first()` / `one_or_none()`.
- **Could match Java?** yes - rename; the docstring already explains the clash.
- **Surprise:** medium

### 12. ~~`version` defaults to 1~~ fixed (`EKB-0292`)

> Now as Java: no default. A builder never given a version refuses with "version cannot be
> null", and so does `increase_version()` with none set. Any setter never called is refused by
> name the same way, not with the constructor's `TypeError`.

- **What:** You can build a model or entity without giving a version and it becomes 1. In Java
  `version` is required: forget `withInitialVersion()` and `build()` throws.
- **Python:** `domain.py:479` (`version: int = 1`). Ran it: `Wallet(...)` without `version`
  gave `version == 1`.
- **Java:** `domain/Entity.java:52`, `domain/Model.java:87` (`Validate.notNull(builder.version…)`).
- **Why:** a choice - a dataclass field with a default.
- **Could match Java?** yes - remove the default (with the cost that every call passes it).
- **Surprise:** medium

### 13. Every field that isn't `X | None` is checked for `None` automatically
- **What:** On construction, any field whose type isn't `X | None` raises
  `ValidationError("balance cannot be null")` if given `None`, including the application's own
  fields. Java checks only the base fields plus what each model's constructor checks itself. If
  the type hints can't be resolved (for example a name imported only under `TYPE_CHECKING`), the
  check is quietly switched off for the whole class.
- **Python:** `domain.py:310-343` (`_required`, and the `except Exception` at `:331`),
  `domain.py:511-527`. Ran it: `balance=None` raised `ValidationError`.
- **Java:** `domain/Model.java:80-89`, `domain/Entity.java:49-54` (base fields only).
- **Why:** a choice - a dataclass can list its own fields, so the rule is written once.
- **Could match Java?** yes - check only the base fields, but that would lose a useful check.
- **Surprise:** medium

### 14. ~~A model that writes its own `__post_init__` must call `super().__post_init__()`~~ fixed

**Fixed in `EKB-0497`.** A model's own `__post_init__` is wrapped when its class is defined
(`domain._guard_post_init`): the built-in checks run first, then the model's code, then its
`validate()` - now required on every model and entity, Java's constructor body - whether or not
it calls `super().__post_init__()`. When it does, nothing runs twice. Proven by
`tests/unit/test_validate.py` (`version=0` and `balance=None` refused without `super()`; a
counter shows one run with it). The text below is the finding as it was.

- **What:** If you add a `__post_init__` to a model and forget `super().__post_init__()`, all the
  base checks and the timestamp handling are silently skipped. In Java the base constructor always
  runs.
- **Python:** `domain.py:520-521` (documented), `domain.py:632-660`.
- **Java:** `domain/Model.java:80` (the `protected Model(Builder)` constructor, which every
  subclass has to call).
- **Why:** Python forces it - dataclasses do not chain `__post_init__`.
- **Could match Java?** no - can only be documented (or checked by a test helper).
- **Surprise:** medium

### 15. An event names its model with a string, and forgetting it fails only when an event is created
- **What:** `class WalletMoneyDeposited(ModelEvent["Wallet"])` with `model_id=` passed as a
  keyword. Leave out the `["Wallet"]` and the class is still defined; the error comes when the
  first event is built. Java won't compile without `super(id, Wallet.class)`.
- **Python:** `domain.py:62`, `domain.py:102-119`.
- **Java:** `domain/ModelEvent.java:37-41`.
- **Why:** Python forces it - no compile step.
- **Could match Java?** partly - it could raise when the class is defined (in
  `__init_subclass__`) rather than at the first instance.
- **Surprise:** medium

### 16. Two events are equal when their fields match, even about different models
- **What:** Events are frozen dataclasses with value equality, and `model_id` is left out of the
  comparison. `Deposited(model_id="a", amount=1) == Deposited(model_id="b", amount=1)` is `True`.
  A Java event is a plain class, equal only to itself.
- **Python:** `domain.py:87` (`model_id: str = field(compare=False)`). Ran it: `True`.
- **Java:** `domain/ModelEvent.java:23` (no `equals`); example
  `ekbatan-examples/.../events/WalletCreatedEvent.java:12`.
- **Why:** a Python convention (dataclass equality) plus a choice (`compare=False` on `model_id`).
- **Could match Java?** yes - include `model_id` in equality, or use `eq=False`.
- **Surprise:** medium

### 17. Exceptions have different names, a common base, and different constructors
- **What:** `StaleRecordError`, `EntityNotFoundError`, `CrossShardError`, `LockAcquisitionError`
  (Java: `...Exception`). All share `EkbatanError` (Java has no common base; all are
  `RuntimeException`). Where Java throws `NullPointerException` / `IllegalArgumentException` /
  `IllegalStateException`, Python mostly raises one `ValidationError`, which is also a
  `ValueError`; `EntityNotFoundError` is also a `LookupError`. `EntityNotFoundError(model, key)`
  takes two arguments where Java takes a message. `UnsupportedError` and `ConnectivityError` exist
  only here (Java uses `UnsupportedOperationException`). Two matches in `find_one_where` raise
  `ValidationError` instead of jOOQ's `TooManyRowsException`.
- **Python:** `errors.py:24-102`; `repository.py:634`.
- **Java:** `repository/exception/EntityNotFoundException.java`,
  `repository/exception/StaleRecordException.java`, `shard/CrossShardException.java`,
  `concurrent/LockAcquisitionException.java`, `internal/Validate.java`.
- **Why:** a Python convention (`Error` suffix, built-in base classes) plus a choice (one base).
- **Could match Java?** partly - names could end in `Exception`, but Python style says `Error`.
- **Surprise:** medium

### 18. `unknown_shard_policy` is a string, not an enum
- **What:** `DatabaseRegistry(..., unknown_shard_policy="fail")` or `"use_default_shard"`. Java
  has an `UnknownShardPolicy` enum (`USE_DEFAULT_SHARD`, `FAIL`). There is nothing to import, and a
  typo is caught only by a type checker.
- **Python:** `persistence.py:252`, `persistence.py:463`.
- **Java:** `shard/UnknownShardPolicy.java:22`, `shard/DatabaseRegistry.java:264`.
- **Why:** a choice - a `Literal` string is common in Python.
- **Could match Java?** yes - add an `UnknownShardPolicy` enum (a `StrEnum` would still accept the
  strings).
- **Surprise:** medium

### 19. The clock is the framework's own, and `VirtualClock` lives in the core package - settled by the user (2026-10-24)
- **Settled by the user (2026-10-24)**: kept as it is.
- **What:** `Clock` is a small interface whose one method is `instant()` (since `EKB-0419`; it
  was `now()`), giving UTC as a datetime with no zone attached; Java uses the JDK's
  `java.time.Clock` and `clock.instant()`. `VirtualClock` is exported from `ekbatan` itself
  (Java: `ekbatan-test-support`). `VirtualClock(instant)` starts *frozen* at that instant; Java's
  one-argument constructor takes another `Clock` (or zone) and starts in real time. `advance()` on
  a running clock raises `EkbatanError` (Java: `IllegalStateException`).
- **Python:** `clock.py:21-24`, `clock.py:39-97`, `__init__.py:36`.
- **Java:** `ekbatan-test-support/src/main/java/io/ekbatan/testsupport/time/VirtualClock.java:31-60,
  97`; `action/ActionExecutor.java:411` (`Clock.systemUTC()`).
- **Why:** Python forces part of it (there is no standard clock object); the rest is a choice.
- **Could match Java?** partly - the method name matches since `EKB-0419`; the constructor could
  match; there is no standard clock type to reuse.
- **Surprise:** medium

### 20. `ShardedId.shard` refuses an id that isn't a version 7 UUID
- **What:** `ShardedId.of(some_uuid4).shard` raises `ValidationError`. Java's
  `resolveShardIdentifier()` reads the bits from any UUID and returns some shard.
- **Python:** `ids.py:184-196`. Ran it: raised `ValidationError`.
- **Java:** `domain/ShardedId.java:63-65`, `shard/ShardedUUID.java` (`resolveShardIdentifier`
  doesn't check the version).
- **Why:** a choice - a version 4 UUID has no shard, and Java's answer is random.
- **Could match Java?** yes - but it would bring back routing to a random shard.
- **Surprise:** low

### 21. The registries can be built straight from a constructor, and the repository registry is stricter
- **What:** `RepositoryRegistry([wallets, alerts])` and `DatabaseRegistry({shard: manager})` work
  without a builder (Java has private constructors and only builders). The builders come from
  `RepositoryRegistry.builder()` instead of Java's `RepositoryRegistry.Builder.repositoryRegistry()`.
  `with_model_repository(repo)` takes no model class (Java: `withModelRepository(Wallet.class,
  repo)`). Registering two repositories for one model raises (Java keeps the last one), and
  asking for a model with no repository raises (Java returns `null`).
- **Python:** `repository.py:1202-1260`, `repository.py:1291`, `persistence.py:248-284`.
- **Java:** `repository/RepositoryRegistry.java:26-72`, `shard/DatabaseRegistry.java:55, 228`.
- **Why:** a choice.
- **Could match Java?** yes - for the factory name and the class argument; the stricter checks are
  better kept.
- **Surprise:** low

### 22. Database wiring is built from ~~SQLAlchemy engines~~ data sources, with shorter names
- **Engine part fixed** (`EKB-0524`, the user's naming 2026-10-24): a manager takes `DataSource`s
  (`AsyncDataSource`s in `ekbatan.aio`), built only with `DataSource.from_config(config)` - Java's
  `hikariConnectionProvider(cfg)` - and an `Engine` is refused; `engine_for` is gone. The shorter
  names and the constructor shape below are unchanged. See `docs/DATASOURCE-LEDGER.md`.
- **What:** `TransactionManager(shard, primary_engine, replica_engine=None, dialect="", label="")`
  takes SQLAlchemy `Engine`s, with the shard first and required (Java: `(primaryProvider,
  secondaryProvider, dialect[, shardIdentifier])`, shard defaults to `DEFAULT`). Engines come from
  `engine_for(config)`. On the registry, `manager(shard)` / `default` / `shards` stand for
  `transactionManager(id)` / `defaultTransactionManager()` / `allTransactionManagers()`, and Java's
  public `primary` / `secondary` maps don't exist. Transactions are `with tm.transaction() as
  tx:` instead of `inTransaction(dsl -> …)` (and an exception is re-raised as-is, not wrapped in
  `RuntimeException`). The `Transaction` docstring mentions `with registry.transaction()`, which does
  not exist.
- **Python:** `persistence.py:93-112`, `persistence.py:119`, `persistence.py:79`
  (docstring), `persistence.py:308-356`, `persistence.py:380`.
- **Java:** `persistence/TransactionManager.java:65-90, 137`, `shard/DatabaseRegistry.java`.
- **Why:** Python forces the engine type; the names are a choice (listed in `DIVERGENCE.md`
  "Names that still differ").
- **Could match Java?** partly - names yes; the `with` block replaces the callback for good.
- **Surprise:** low

### 23. A repository takes a `clock=` that it never uses
- **What:** `AbstractRepository(..., clock=…)` stores `self.clock` and nothing reads it. The
  examples pass one through. Java's repositories take no clock.
- **Python:** `repository.py:214`, `repository.py:221` (the only uses);
  `ekbatan-py-examples/fastapi_wallet_rest_mysql/repository.py:44-47`.
- **Java:** none (`repository/AbstractRepository.java:113-142`).
- **Why:** a choice (unverified: it looks left over from when the repository stamped
  `updated_date`, which `repository.py` `_next_version` now says it no longer does).
- **Could match Java?** yes - remove the argument.
- **Surprise:** low

### 24. The sharding strategy has a different shape
- **What:** A custom strategy implements `uses_shard_aware_id` (a property, not a method),
  `shard_for_id(key)` and `shard_for(persistable)`, returning `None` instead of `Optional`. The
  interface isn't generic. `EmbeddedBitsShardingStrategy` also accepts a string, a typed id or a
  `ShardIdentifier` (Java: a `UUID` only). There's no `ShardedUUID` class: `ShardedId.of` takes a
  plain `UUID` or string, and the bit work is the functions `sharded_uuid`, `read_shard`,
  `uuid_instant`.
- **Python:** `sharding.py:128-148`, `sharding.py:185-201`, `sharding.py:84-125`, `ids.py:165`.
- **Java:** `shard/ShardingStrategy.java:28-44`, `shard/EmbeddedBitsShardingStrategy.java`,
  `shard/ShardedUUID.java`, `domain/ShardedId.java:41`.
- **Why:** a Python convention (properties, `None`, functions instead of classes of statics).
- **Could match Java?** partly - method names could be Java's; `Optional` has no Python
  counterpart.
- **Surprise:** low

### 25. Methods and options that exist only in Python
- **What:** `evolve(**changes)` (and `evolve(emit=…)` on a model), `without_events()`,
  `is_deleted`, `Id.shard`, `Id.minted_at`, `ShardedId.random()`, `ShardedId.minted_at`, the
  `shard=` argument on `find_where`/`count`/`all`/`exists_where`, `repo.column(name)`,
  `repo.id_matches(key)`, `repo.dialect()`, and `count_statements(registry)` for counting SQL
  statements in tests. A Java reader won't know what these map to.
- **Python:** `domain.py:564, 577, 688, 697`; `ids.py:129-147, 178-201`; `repository.py:611-680`;
  `accounting.py:52`.
- **Java:** none.
- **Why:** a choice.
- **Could match Java?** yes - by removing them, or by listing them as additions (`DIVERGENCE.md`
  lists only some).
- **Surprise:** low

### 26. Typed values compare across subclasses and print as the bare value
- **What:** A `TypedValue` subclass equals its parent with the same value (Java: exact class
  only), any two typed values can be compared with `<` (Java's `TypedValue` isn't `Comparable`),
  and `str(WalletNumber("x"))` is `x` where Java's `toString()` is `WalletNumber{x}`.
- **Python:** `ids.py:72-103`.
- **Java:** `domain/TypedValue.java:34-49`.
- **Why:** a choice - so a value wrapped by the application equals the same value read back.
- **Could match Java?** yes.
- **Surprise:** low

### 27. A shard prints as `1:2`
- **What:** `str(ShardIdentifier(1, 2))` is `1:2` (in logs and errors); Java prints
  `ShardIdentifier(1, 2)`. Python also lets you call the constructor directly; Java's is private
  (only `of`).
- **Python:** `sharding.py:53-71`. Ran it.
- **Java:** `shard/ShardIdentifier.java:40, 74`.
- **Why:** a choice.
- **Could match Java?** yes.
- **Surprise:** low

### 28. On MySQL, `add` / `update` return a rebuilt object, not the one passed in
- **What:** On MySQL (no `RETURNING`), Python returns `from_row(to_row(model))`, so the result has
  no events and has typed values wrapped again. Java returns the very object passed in (for
  `update`, `nextVersion()` of it, which still carries the events).
- **Python:** `repository.py:740`, `repository.py:861`.
- **Java:** `repository/AbstractRepository.java:359`, `:481`.
- **Why:** a choice - so the next update doesn't write the same events twice.
- **Could match Java?** yes - but it would bring that risk back.
- **Surprise:** low

### 29. Names are snake_case throughout
- **What:** Every method and field is snake_case (`add_no_result`, `created_date`,
  `tx_db_else_db`, `with_initial_version`). Mentioned once here; the items above are about names
  that differ by more than that.
- **Python:** throughout (e.g. `repository.py:150-181`).
- **Java:** throughout (e.g. `repository/Repository.java`).
- **Why:** a Python convention (PEP 8).
- **Could match Java?** no - not without fighting every Python tool.
- **Surprise:** low
