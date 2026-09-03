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
        return self.evolve(
            balance=new_balance,
            emit=WalletMoneyDeposited(amount=amount, new_balance=new_balance),
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

## Status

Early. Working, tested, and typed:

- models, entities and events
- the plan, actions, and the executor
- the single-table JSON outbox, and a seam to replace it
- repositories: reads, batches, the optimistic lock, soft deletion
- sharding: routing by id, per-shard commits, cross-shard refusal
- retry, and the errors that are worth retrying

Not yet: keyed locks, distributed jobs, the local event handler. See
[DIVERGENCE.md](DIVERGENCE.md) for where this port deliberately differs from the Java one.

```
uv sync
uv run pytest        # 35 passing
uv run mypy          # strict, clean
uv run ruff check .
```

Behaviour is pinned by [ekbatan-spec](https://github.com/ekbatan-io/ekbatan-spec), which is
language-independent and shared with every port.
