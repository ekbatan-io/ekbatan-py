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

Schema classes are generated with `sqlacodegen`. Postgres via psycopg 3, MySQL and MariaDB via
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
| **schema and migrations** | the framework's tables as metadata, DDL per dialect, yoyo across every shard |
| **statement accounting** | so the cost claims are checkable rather than asserted |
| **test support** | `run_action` runs a unit of work against a bare plan, with no database |

Only `event-streaming` is missing, and it is not planned: publishing runs inside Kafka Connect,
in a JVM, whichever language wrote the row.

[DIVERGENCE.md](DIVERGENCE.md) records where this port deliberately differs from the Java one.

## Testing

An action's business logic needs no database. `run_action` runs one against a bare plan and
reports what it staged:

```python
staged = run_action(DepositMoney(wallet), DepositParams(amount=Decimal("50.00"), ...))

assert staged.result.balance == Decimal("150.00")
assert staged.emitted(WalletMoneyDeposited)[0].amount == Decimal("50.00")
assert staged.updated(Wallet) == [staged.result]
```

It binds the plan through the same mechanism the executor uses, so `self.plan`, `self.clock`
and `self.principal` behave exactly as they do in production. Nothing is written. Knowing that
state and events land *together* is a test against a database, and no test-support machinery
substitutes for it.

```
uv run pytest                      # SQLite, about two seconds
uv run pytest --dialects=postgres  # in a container
uv run pytest --dialects=all       # every test, on all four
uv run mypy                        # strict
uv run ruff check .
```

125 tests. 82 of them are parametrised over the dialect, so `--dialects=all` runs 371.

There are no per-dialect test classes. Java needs one subclass per dialect per suite -
`PgRepositoryTest`, `MysqlRepositoryTest`, `MariadbRepositoryTest` - because that is how JUnit
parametrises a class. Here one fixture is parametrised, so a test cannot be accidentally left
out of a dialect.

SQLite is the default and is **not** a supported production dialect. It is there because a
suite needing a daemon to run at all is a suite people stop running; the claim that this
framework works is a claim about Postgres, MySQL and MariaDB, and CI runs all three on every
push - plus Postgres again under a non-UTC process, which is where the timestamp defects hide.

## What a job, a handler and a lock look like

```python
class PayStipend(DistributedJob):
    name = "wallet-stipend"  # the primary key of its row, cluster-wide
    schedule = Every(timedelta(days=1))

    def run(self, context: JobContext) -> None:
        for wallet in self.wallets.all():
            self.executor.execute(DepositMoney(self.wallets), DepositParams(...))


class NotifyOnDeposit:
    name = "wallet-deposit-notification"
    event_type = WalletMoneyDeposited

    def handle(self, envelope: EventEnvelope[WalletMoneyDeposited]) -> None:
        self.executor.execute(
            Notify(),
            NotifyParams(
                wallet_id=UUID(envelope.model_id),
                recipient=envelope.action_params["recipient"],  # not in the event, and rightly so
            ),
        )


with locks.acquire(f"wallet:{wallet_id}", timedelta(seconds=30)):
    ...
```

Behaviour is pinned by [ekbatan-spec](https://github.com/ekbatan-io/ekbatan-spec), which is
language-independent and shared with every port.
