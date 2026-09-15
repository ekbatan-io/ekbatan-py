# Models and entities

Two kinds of persistable object. Both immutable, both version-tracked, both with a `state` column
that supports soft deletion. The difference is whether their changes are news.

| | `Model` | `Entity` |
| --- | :---: | :---: |
| `id`, `state`, `version` | yes | yes |
| `created_date` / `updated_date`, managed by the framework | yes | no |
| Changes emit events | yes | no |

Choose **`Model`** when a change should be recorded as an event - for downstream consumers, for
in-process handlers, for audit, or for any reason a discrete record of *what happened* is worth
having.

Choose **`Entity`** when the current state is enough and the history of individual changes is
not: lookup tables, configuration, auxiliary records that take part in an action without needing
a trail of their own.

An entity may add its own timestamp columns; the framework simply will not populate them.

## A model

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[WalletId, WalletState]):
    owner_id: UUID
    currency: str
    balance: Decimal

    @classmethod
    def open(cls, *, owner_id: UUID, currency: str, balance: Decimal) -> Wallet:
        wallet_id = WalletId.random()
        return (
            WalletBuilder()
            .id(wallet_id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(balance)
            .with_initial_version()
            .with_event(WalletOpenedEvent(owner_id=owner_id, currency=currency,
                                          opening_balance=balance))
            .build()
        )

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"deposit amount must be positive, got {amount}")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(MoneyDepositedEvent(amount=amount, new_balance=new_balance))
            .balance(new_balance)
            .build()
        )

    def close(self) -> Wallet:
        if self.state is WalletState.CLOSED:
            return self
        return self.copy().with_event(WalletClosedEvent()).state(WalletState.CLOSED).build()
```

Two patterns worth naming:

- **A factory** (`open`) builds a *new* aggregate, calls `with_initial_version()` and attaches
  the creation event.
- **`copy()`** returns a builder pre-populated with the current state. Mutators build on it,
  change what changed, attach an event and `build()`. The previous instance is untouched.

`close()` returning `self` when already closed is the shape to copy: nothing staged means
nothing emitted, and the action's invocation is still recorded as a sentinel row.

### `copy()` versus `evolve()`

`evolve()` is the framework's own shorthand and exists because internal code needs it. Domain
code should prefer `copy()`, so that every change in an application reads the same way.

## Events

A `ModelEvent` is a frozen dataclass carrying whatever the domain needs:

```python
@dataclass(frozen=True, kw_only=True)
class MoneyDepositedEvent(ModelEvent):
    amount: Decimal
    new_balance: Decimal
```

That is the whole declaration. **There is no deserialisation boilerplate**, and this is one of
the few places this port is straightforwardly ahead of Java: Java needs a `@JsonCreator`
constructor with a `@JsonProperty` for every field, or the dispatch job cannot rebuild the event
and the handler is never invoked. Here the payload is rebuilt from the dataclass's own
annotations - a `Decimal` from its string, a `datetime` from its ISO form, an enum from its name.
Nothing to write, and nothing to forget.

Events are written to `events.payload` as canonical JSON. See [The outbox](outbox.md) for the
row, including why two identity keys are spelled in Java's style.

## Optimistic locking, always

Every persistable carries a `version`, and every update carries it:

```sql
UPDATE wallets
SET balance = ?, version = ?
WHERE id = ? AND version = ?
```

If another transaction got there first, zero rows match and `StaleRecordError` unwinds the
action's transaction. The default policy retries the whole action once after 100 ms - see
[Actions](actions.md#retries).

**The write path takes no pessimistic row locks, ever.** Conflicts surface as an exception rather
than as blocked threads. When you genuinely want a lock, take a keyed lock or issue
`SELECT ... FOR UPDATE` yourself.

The interesting case is when losing is the wrong outcome. Optimistic locking is correct but it is
correct by *failing*: two deposits race, the loser raises. Taking a keyed lock on the wallet
first means the second caller waits and then succeeds, which is what anyone depositing into a
busy wallet actually wants. See [Wiring with FastAPI](../wiring-fastapi.md).

## Soft deletion

Rows are not physically removed. `state` flips to `DELETED` and every read filters them out -
the filter is applied once, in the framework, rather than remembered at each call site.

Deleting is a domain event like any other. The row survives: it is still the other side of every
event already published about that aggregate. What leaves is the wallet, not its history.

When a physical delete is genuinely required - an erasure request, purging expired data - issue
the `DELETE` yourself through the repository's table.

## Builders, and the one thing that is generated

`wallet.copy().balance(...)` works for every model with nothing generated, and refuses an unknown
field at the point of the call. What it cannot do is tell a *type checker* that `.balance(...)`
takes a `Decimal`: setters are resolved by name at runtime, so a checker sees only
`__getattr__`.

That is the one thing worth generating, and the only thing generated:

```
uv run python -m ekbatan.codegen myapp.model --out myapp/builders.py
```

```python
class WalletBuilder(ModelBuilder[Wallet, WalletId, WalletState]):
    builds = Wallet

    def owner_id(self, value: UUID) -> Self: ...
    def currency(self, value: str) -> Self: ...
    def balance(self, value: Decimal) -> Self: ...
```

A generated builder declares **only the fields its model added**. `id`, `state`, `version` and
the timestamps are typed on the base through its parameters - the same trick Java's
`Model.Builder<ID, B, M, STATE>` uses, and the reason a generated builder there declares three
methods rather than eight. An entity gets `EntityBuilder`, so it is not offered setters for
timestamps and events it does not have.

Everything else Java's `@AutoBuilder` emits - constructing, copying, incrementing a version -
needs no generation here, so none is done. The output is ordinary Python: **commit it**, read it,
regenerate when a model changes. A stale one is a type error rather than a wrong answer, because
`build()` still calls the real constructor.

Note the difference from `schemagen`, whose output is *gitignored*: a builder is derived from
source you already have, a schema from a database you have to start.

## Naming

- **Model** - a singular noun: `Wallet`, `Order`, `Subscription`.
- **State enum** - `<Model>State` with upper-snake values: `WalletState.OPENED`. `GenericState`
  (`ACTIVE` / `DELETED`) is there for entities and simple models that need no state machine.
- **Event** - `<Noun><Verb>ed` + `Event`, because an event describes something that already
  happened: `WalletOpenedEvent`, `MoneyDepositedEvent`, `TransferFailedEvent`.
- **Action** - imperative, because an action describes intent: `OpenWalletAction`,
  `DepositMoneyAction`.

**One divergence, deliberately.** Java orders an action's name model-first -
`WalletCreateAction`, `WalletDepositMoneyAction`. This port orders it verb-first:
`OpenWalletAction`, `DepositMoneyAction`. The suffix and the imperative mood are the same; the
word order is not. Java's ordering groups a model's actions together alphabetically, which is
worth something in a large package tree; verb-first reads as the English sentence the action
actually is, which is worth more in a flat Python module. Pick one and hold it - the examples
here are all verb-first.

## See also

- [Actions](actions.md) - how these flow through `perform()` into the database
- [The outbox](outbox.md) - where the events go
- [Getting started](../getting-started.md) - the repository that maps a model to a row
