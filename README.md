# ekbatan-py

A persistence framework for event-driven systems, with an ergonomic built-in outbox pattern.
The Python port of [Ekbatan](https://github.com/ekbatan-io/ekbatan).

A service that publishes domain events usually writes twice - once to its database, once to its
broker - and the two disagree whenever the second write fails. The transactional outbox is the
established answer, normally bolted on as a separate library or assembled by hand.

This folds it into the persistence layer, with three pieces.

**A model emits an event when it changes.**

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[UUID, WalletState]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def deposit(self, amount: Decimal) -> "Wallet":
        if amount <= 0:
            raise ValidationError(f"deposit amount must be positive, got {amount}")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(MoneyDepositedEvent(model_id=str(self.id), amount=amount, new_balance=new_balance))
            .balance(new_balance)
            .build()
        )
```

**An action stages changes on a plan instead of writing them.**

```python
class DepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))
```

**The executor opens one transaction, writes the rows and their events, and commits.**

```python
executor.execute(deposit_money_action, DepositParams(wallet_id=..., amount=Decimal("50.00"), ...))
```

That is the whole surface for the common case. There is no transaction to open, nothing to
flush, nothing to publish, and no mention of an outbox anywhere above - which is the point.
State and events land together or neither does.

## Install

```
uv add ekbatan[postgres]          # or [mysql], or [mariadb]
```

The extras are the drivers and the optional pieces, and nothing you do not ask for is installed.
`postgres` brings psycopg 3, `mysql` and `mariadb` bring PyMySQL, `migrations` brings Alembic,
`redis` brings the Redis-backed keyed lock, and `codegen` brings what `schemagen` needs to start
a container at build time - a development dependency, not a runtime one:

```
uv add --dev ekbatan[codegen]
```

Python 3.13 or newer. SQLAlchemy 2.0 is the only unconditional dependency, alongside
`ekbatan-py-db-scheduler` - the port of `kagkarlsson/db-scheduler`, which this repository also
publishes because there is no upstream for it on PyPI.

The package is typed and ships `py.typed`, so a checker reads its annotations rather than
ignoring them - which is also why the classifiers claim `Typing :: Typed` truthfully.

## Versioning and support

Semantic versioning, and a version number shared with nothing else: **this port's versions do not
track the Java framework's.** `ekbatan-py` 0.4 does not mean `ekbatan` 0.4. What the two do share
is the on-disk contract - the outbox schema, the lock names, the `scheduled_tasks` table - and
that is pinned by [ekbatan-spec](https://github.com/ekbatan-io/ekbatan-spec) rather than by a
version number. A Java service and a Python service interoperate when both pass the same spec
revision, whatever they call themselves.

While the major version is 0, a minor bump may break an API. Pin accordingly: `ekbatan~=0.4.0`.
Anything reached through `ekbatan.<name>` is public; anything under an underscore is not, and the
`_holder` module is the example - it exists for the keyed lock and will change without notice.

Releases are cut from this repository to PyPI with trusted publishing - no token is stored
anywhere, and every artifact carries a PEP 740 attestation. [RELEASE.md](RELEASE.md) is the
procedure.

## Built on

SQLAlchemy 2.0, used as a typed statement builder rather than as an ORM. Declarative classes
describe the schema, because that is where the type information lives - `select(Wallet.id,
Wallet.balance)` checks as `Select[tuple[UUID, Decimal]]` - and statements execute on a
`Connection`. The one this framework hands you is `Db`, a subclass that adds `rows(...)` for
turning rows into objects and takes nothing away. There is no `Session` anywhere: its identity
map and autoflush are a second unit-of-work implementation, and two of those fighting over one
transaction is the worst thing you could do to this design.

Table definitions are generated with `python -m ekbatan.schemagen`, which starts a container,
runs your migrations into it and reflects the result - the counterpart to Java's jOOQ step.
Postgres via psycopg 3, MySQL and MariaDB via PyMySQL. Migrations with Alembic, driven as a
library rather than from an `alembic.ini`, so one call migrates every shard.

## What is here

| | |
|---|---|
| **models and entities** | frozen dataclasses; a change is `evolve(..., emit=...)` |
| **the plan and the executor** | one transaction per shard, rows then events, retry on a lost race |
| **the outbox** | one table, canonical JSON, and a seam to replace the whole store |
| **repositories** | field-name mapping, optimistic locking, soft deletion, batches as one statement |
| **sharding** | shard bits in the id, per-shard commits, cross-shard refused by default |
| **keyed locks** | in-process, Postgres advisory, MySQL and MariaDB `GET_LOCK`; reentrant, watchdogged |
| **the local event handler** | fan-out and dispatch, at-least-once, capped backoff, retention |
| **distributed jobs** | one slot, one execution, across the cluster |
| **schema and migrations** | the framework's tables as metadata, DDL per dialect, Alembic across every shard |
| **statement accounting** | so the cost claims are checkable rather than asserted |
| **test support** | `ActionSpec` / `ActionAssert` run a unit of work against a bare plan, with no database - Java's, method for method |
| **typed builders** | `python -m ekbatan.codegen` - the one thing Python cannot infer |

Only `event-streaming` is missing, and it is not planned: publishing runs inside Kafka Connect,
in a JVM, whichever language wrote the row.

[DIVERGENCE.md](DIVERGENCE.md) records where this port deliberately differs from the Java one,
and [docs/SHORTCOMINGS.md](docs/SHORTCOMINGS.md) records where it is simply behind - what is
missing, what is slower, and what has not been decided yet.

## Documentation

**[docs/](docs/README.md)** is the index. The short version:

- [Concepts](docs/concepts/README.md) - the dual-write trap, the outbox, actions, models and
  entities, sagas, sharding
- [Getting started](docs/getting-started.md) - install, the extras, your first action
- [Wiring](docs/wiring.md) and [with FastAPI](docs/wiring-fastapi.md) - the complete bootstrap
- [Database](docs/database/README.md) - tables, repositories, transactions, keyed locks,
  sharding, migrations, the four dialects, schemagen
- [Events out](docs/events/README.md) and [Jobs](docs/jobs/README.md) - in-process handlers,
  Debezium, cluster-exclusive background work
- [Concurrency](docs/runtime/concurrency.md) - threads, the GIL, asyncio, and the one way to use
  this wrongly
- [Observability](docs/runtime/observability.md) - spans, metrics, what to alert on
- [Releasing](RELEASE.md) - how a release is cut

## Typed builders

`wallet.copy().balance(...)` works for every model with nothing generated, and refuses an
unknown field at the point of the call. What it cannot do is tell a *type checker* that
`.balance(...)` takes a `Decimal`: the setters are resolved by name at runtime, so a checker
sees only `__getattr__`.

That is the one thing worth generating, and the only thing this generates:

```
uv run python -m ekbatan.codegen myapp.model --out myapp/builders.py
```

```python
wallet(existing).balance("oops").build()
# error: Argument 1 to "balance" of "WalletBuilder" has incompatible type "str";
#        expected "Decimal"
```

A generated builder declares **only the fields its model added**:

```python
class WalletBuilder(ModelBuilder[Wallet, ShardedId[Wallet], WalletState]):
    builds = Wallet

    def owner_id(self, value: UUID) -> Self: ...
    def currency(self, value: str) -> Self: ...
    def balance(self, value: Decimal) -> Self: ...
```

`id`, `state`, `version` and the timestamps are typed on the base through those parameters -
the same trick `Model.Builder<ID, B, M, STATE>` uses in Java, and the reason a generated
builder there declares three methods rather than eight. An entity gets `EntityBuilder` instead of
`ModelBuilder`, so it is not offered setters for timestamps and events it does not have.

Everything else Java's `@AutoBuilder` emits - constructing, copying, incrementing a version -
already needs no generation here, so none is done. The output is ordinary Python: commit it,
read it, regenerate it when a model changes. A stale one is a type error rather than a wrong
answer, because `build()` still calls the real constructor.

`ekbatan-py-examples/wallet_service_sqlite` uses it, including the three-line `copy()` override that makes a
model's own methods typed too.

## Layout

```
src/ekbatan/          the library
db-scheduler/         its own distribution: the port of kagkarlsson/db-scheduler
tests/
  fixtures/           the domain the suite is written against - owned by the suite
  unit/               no database, ever. 250 tests, about a second
  integration/        everything that touches one. 322 tests, on any dialect
ekbatan-py-examples/
  fastapi_wallet_rest_pg/          the REST service, on PostgreSQL
  fastapi_wallet_rest_mysql/       the same service, on MySQL
  fastapi_wallet_rest_mariadb/     the same service, on MariaDB
  fastapi_wallet_rest_sharded_pg/  the same service, over two databases
  fastapi_wallet_saga_pg/          a saga: two services, choreography, compensation
  job_worker_pg/                   a distributed job, no HTTP at all
  wallet_service_sqlite/           the smallest one. No container, no web stack
```

One REST example per dialect and one of everything else, which is the shape of Java's matrix -
there, too, only the REST project is replicated per database. Each is standalone: it imports the
framework and is imported by nothing.

Three separate things with three separate jobs, and they do not import each other. The suite's
domain exists to exercise every shape worth exercising, including a few no application would
want; the example exists to show what good code looks like. Coupled, every change made for
coverage lands in the documentation and every change made for clarity breaks the suite.
`tests/test_structure.py` enforces the separation, including that nothing in `unit/` asks for a
database.

## Testing

An action's business logic needs no database. `ActionSpec` runs one against a bare plan - Java's
`ActionSpec` / `ActionAssert`, method for method:

```python
(ActionSpec.of(DepositMoneyAction(wallets))
    .execute(DepositParams(amount=Decimal("50.00"), ...))
    .assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
    .assert_emitted(MoneyDepositedEvent, 1)
    .assert_no_additions())
```

Or, as plain data, with `run_action`:

```python
staged = run_action(DepositMoneyAction(wallets), DepositParams(amount=Decimal("50.00"), ...))

assert staged.result.balance == Decimal("150.00")
assert staged.emitted(MoneyDepositedEvent)[0].amount == Decimal("50.00")
assert staged.updated(Wallet) == [staged.result]
```

It binds the plan through the same mechanism the executor uses, so `self.plan`, `self.clock`
and the `principal` argument behave exactly as they do in production. Nothing is written. Knowing that
state and events land *together* is a test against a database, and no test-support machinery
substitutes for it.

```
uv run pytest tests/unit              # no database at all, about a second
uv run pytest                         # the whole suite, on SQLite
uv run pytest --dialects=postgres     # in a container
uv run pytest --dialects=all          # every test, on all four
uv run pytest ekbatan-py-examples                # the examples' own tests
cd ekbatan-py-examples && uv run python -m wallet_service_sqlite

uv run mypy                           # strict, on the library and the examples
uv run ruff check .
```

578 tests. 264 of them are parametrised over the dialect, so `--dialects=all` runs 1,370.

There are no per-dialect test classes. Java needs one subclass per dialect per suite -
`PgRepositoryTest`, `MysqlRepositoryTest`, `MariadbRepositoryTest` - because that is how JUnit
parametrises a class. Here one fixture is parametrised, so a test cannot be accidentally left
out of a dialect.

SQLite is the default and is **not** a supported production dialect. It is there because a
suite needing a daemon to run at all is a suite people stop running; the claim that this
framework works is a claim about Postgres, MySQL and MariaDB, and CI runs all three on every
push - plus Postgres again under a non-UTC process, which is where the timestamp defects hide.

Behaviour is pinned by [ekbatan-spec](https://github.com/ekbatan-io/ekbatan-spec), which is
language-independent and shared with every port.
