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
            .with_event(WalletMoneyDeposited(amount=amount, new_balance=new_balance))
            .balance(new_balance)
            .build()
        )
```

**An action stages changes on a plan instead of writing them.**

```python
class DepositMoney(Action[DepositParams, Wallet]):
    def __init__(self, wallets: Repository[Wallet, UUID]) -> None:
        self.wallets = wallets

    def perform(self, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))
```

**The executor opens one transaction, writes the rows and their events, and commits.**

```python
executor.execute(deposit_money, DepositParams(wallet_id=..., amount=Decimal("50.00"), ...))
```

That is the whole surface for the common case. There is no transaction to open, nothing to
flush, nothing to publish, and no mention of an outbox anywhere above - which is the point.
State and events land together or neither does.

## Built on

SQLAlchemy 2.0, used as a typed statement builder rather than as an ORM. Declarative classes
describe the schema, because that is where the type information lives - `select(Wallet.id,
Wallet.balance)` checks as `Select[tuple[UUID, Decimal]]` - and statements execute on a plain
`Connection`. There is no `Session` anywhere: its identity map and autoflush are a second
unit-of-work implementation, and two of those fighting over one transaction is the worst thing
you could do to this design.

Table definitions are generated with `python -m ekbatan.schemagen`, which starts a container,
runs your migrations into it and reflects the result - the counterpart to Java's jOOQ step. Postgres via psycopg 3, MySQL and MariaDB via
PyMySQL. Migrations with yoyo, which runs plain per-dialect SQL files.

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
| **test support** | `run_action` runs a unit of work against a bare plan, with no database |
| **typed builders** | `python -m ekbatan.codegen` - the one thing Python cannot infer |

Only `event-streaming` is missing, and it is not planned: publishing runs inside Kafka Connect,
in a JVM, whichever language wrote the row.

[DIVERGENCE.md](DIVERGENCE.md) records where this port deliberately differs from the Java one,
and [docs/SHORTCOMINGS.md](docs/SHORTCOMINGS.md) records where it is simply behind - what is
missing, what is slower, and what has not been decided yet.

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
class WalletBuilder(ModelBuilder[Wallet, WalletId, WalletState]):
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
tests/
  fixtures/           the domain the suite is written against - owned by the suite
  unit/               no database, ever. 56 tests, about a second
  integration/        everything that touches one. 118 tests, on any dialect
ekbatan-py-examples/
  wallet_service_sqlite/     a standalone application. Imports only ekbatan; imported by nothing
```

Three separate things with three separate jobs, and they do not import each other. The suite's
domain exists to exercise every shape worth exercising, including a few no application would
want; the example exists to show what good code looks like. Coupled, every change made for
coverage lands in the documentation and every change made for clarity breaks the suite.
`tests/test_structure.py` enforces the separation, including that nothing in `unit/` asks for a
database.

## Testing

An action's business logic needs no database. `run_action` runs one against a bare plan and
reports what it staged:

```python
staged = run_action(DepositMoney(wallet), DepositParams(amount=Decimal("50.00"), ...))

assert staged.result.balance == Decimal("150.00")
assert staged.emitted(MoneyDeposited)[0].amount == Decimal("50.00")
assert staged.updated(Wallet) == [staged.result]
```

It binds the plan through the same mechanism the executor uses, so `self.plan`, `self.clock`
and `self.principal` behave exactly as they do in production. Nothing is written. Knowing that
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

175 tests. 114 of them are parametrised over the dialect, so `--dialects=all` runs 517.

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
