# Models and entities

Two kinds of persistable object. Both immutable, both version-tracked, both with a `state` column
that supports soft deletion. The difference is whether their changes are news.

| | `Model` | `Entity` |
| --- | :---: | :---: |
| `id`, `state`, `version` | yes | yes |
| `created_date` / `updated_date` | **required**, and typed on the base | neither |
| Changes emit events | yes | no |

Choose **`Model`** when a change should be recorded as an event - for downstream consumers, for
in-process handlers, for audit, or for any reason a discrete record of *what happened* is worth
having.

Choose **`Entity`** when the current state is enough and the history of individual changes is
not: lookup tables, configuration, auxiliary records that take part in an action without needing
a trail of their own.

An entity may add its own timestamp columns; the framework simply will not populate them.

## A Model

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[WalletId, WalletState, WalletBuilder]):
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

### If you are coming from Django or the SQLAlchemy ORM

`deposit()` returning a *new* wallet instead of assigning to `self.balance` is the part that
looks strange, so it is worth saying plainly what it is and is not.

`@dataclass(frozen=True, kw_only=True)` is ordinary, idiomatic Python - the standard library has
had dataclasses since 3.7, and pydantic, pytest, anyio, FastAPI and SQLAlchemy all use frozen
ones. What is unusual is *what* is frozen here. The ecosystem freezes **value objects**: cache
keys, config values, durations, markers. This framework freezes the **aggregate**.

That is a deliberate stance rather than a Python convention, and the reason is
[two-phase execution](actions.md#the-two-phase-lifecycle). The plan holds a *reference* to what you
staged, and the executor writes it later, inside a transaction. With a mutable model, anything
touching that object between staging and commit would silently change what got written - a
deposit of 100 committing as 0. Frozen makes that unwritable rather than merely discouraged.

What you give up is ORM-style dirty tracking. There is no `session.commit()` working out what
changed; you say what changed, explicitly, through the plan. That is more typing, and it is what
makes the outbox guarantee expressible at all.

The Java port takes the identical stance - `public final` fields, a `copy()` builder, a `deposit`
that returns a new `Wallet`. This is not a Python-side invention.

`kw_only` is doing two jobs, and the second is not optional. It stops transposition, because
`owner_id` sits next to `currency` and `started_date` next to `completion_date` - adjacent
same-typed fields that swap silently when passed positionally. It also makes the inheritance
legal at all: the base carries `version` and the timestamps, all with defaults, and a subclass
field without one would otherwise be *"non-default argument follows default argument"*, a
`TypeError` at class-definition time. Keyword arguments have no order, so that cannot arise.

### `copy()` versus `evolve()`

`evolve()` is the framework's own shorthand and exists because internal code needs it. Domain
code should prefer `copy()`, so that every change in an application reads the same way.

## ModelEvent

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

### Deserialization (for in-process handlers)

Events are stored as JSON in `events.payload` and read back into the same class when an
in-process handler is invoked. **Nothing extra is needed for that round trip** — no annotation,
no registered constructor, no per-handler mapping layer. The payload is rebuilt from the
dataclass's own annotations, which is the one place the field types are already written down.

That is the whole of the Python side of a problem Java spends a `@JsonCreator` constructor on.
Java's Jackson cannot reconstruct an event with constructor parameters unless you write a second
constructor annotating every parameter with its JSON name; forget it and the dispatch job fails
to invoke the handler at all. Here the dataclass's `__init__` already takes keyword arguments
named after its fields, so there is nothing to declare.

What the decoder does with each field, driven by its annotation:

| Annotation | Rebuilt from |
|---|---|
| `Decimal` | its **string**, at the scale it was written |
| `datetime` | its ISO-8601 form, back to an aware UTC instant |
| `UUID` | its canonical text |
| an `Enum` | its **member name**, never its ordinal |
| a nested frozen dataclass | recursively, the same way |
| `list` / `tuple` / `set` / `frozenset` of any of the above | element by element |
| `T \| None` | the non-`None` arm, when there is exactly one |

Decimals travel as strings deliberately: a JSON number is a double, and a balance that
round-trips through one is no longer the balance. Enums travel by name for the same class of
reason — an ordinal changes meaning the moment somebody inserts a member in the middle, and
nothing about that change looks dangerous at the time.

Two limits worth knowing:

- **A key the dataclass does not declare is dropped**, not an error. An additive change on the
  producer cannot break a consumer that has not caught up — which is the same tolerance the
  streaming path has, and for the same reason.
- **A union with more than one non-`None` arm is left as raw JSON.** There is no discriminator in
  the payload to pick an arm with, so the decoder does not guess. Model that as a nested
  dataclass with a `kind` field rather than as a union if a handler needs it typed.

`model_id` and `model_name` arrive under Java's spelling — `modelId`, `modelName` — because its
local event handler deserialises the payload straight into the event class and refuses one
without them. They are read back under this port's names, so a payload written by *either* side
rebuilds a complete event here.

Streaming-only consumers do not go through any of this: they read the JSON off the broker.

`created_date` is **required**, exactly as Java's is — its constructor says
`Validate.notNull(builder.createdDate)` and `build()` throws without it. A model records
something that happened, and when it happened is part of the record rather than a column the
framework backfills afterwards. Pass `self.clock.now()` from the action, which is what Java's
`clock.instant()` is doing:

```python
Wallet.open(owner_id=..., currency="EUR", balance=..., created_date=self.clock.now())
```

`updated_date` falls back to `created_date` — a row that has never changed was last updated when
it was created. Both are normalised to zone-less UTC on the way in, the analogue of Java's
`.truncatedTo(MICROS)`: an aware value stored as-is would not compare equal to the same instant
read back.

**Nothing moves `updated_date` but you.** Neither the plan nor the repository touches it on a
write, exactly as nothing in Java's `ekbatan-core` writes `updatedDate`. Move it on the changes
where it means something:

```python
def deposit(self, amount: Decimal, at: datetime) -> Wallet:
    return (
        self.copy()
        .with_event(MoneyDepositedEvent(amount=amount, new_balance=self.balance + amount))
        .balance(self.balance + amount)
        .updated_date(at)
        .build()
    )
```

The trade is worth knowing rather than discovering: a column named `updated_date` does not move
on its own, and a row you never mark still reads as last-updated-when-created. That is the cost
of a framework that will not rewrite a column your application set — which is the property that
makes the column usable for recording when *your* change happened.

The **version** is different, and does move on its own: the repository increments it inside the
write, under `WHERE version = ?`, because that is the optimistic lock rather than a convenience.
Java's `nextVersion()` does the same.

`Entity` has neither, in either port. Something that needs to know when it last changed almost
certainly has changes worth telling the rest of the system about, which makes it a `Model`.

## Required, and optional

The base refuses `None` for any field whose annotation does not admit it — **including the ones
you declare**. `owner_id: UUID` is required; `note: str | None` is not.

| Java | here | `None` |
|---|---|---|
| `String currency` | `currency: str` | refused |
| `Optional<String> note` | `note: str \| None` | accepted |

Java writes this out per field — `Validate.notNull(builder.ownerId, ...)` once for each, in
every model's constructor — because a Java base class cannot enumerate a subclass's fields. A
dataclass can, so it is one rule rather than three lines per model, and it covers the fields
somebody would have forgotten.

There is no counterpart to Java's fourth case, a `null` where an `Optional` *reference* belongs:
Python's absence **is** `None` rather than a wrapper that can itself be missing. One sentinel,
not two.

## Optimistic locking, always

Every persistable carries a `version`, and every update carries it:

```sql
UPDATE wallets
SET balance = ?, version = ?
WHERE id = ? AND version = ?
```

If another transaction got there first, zero rows match and `StaleRecordError` unwinds the
action's transaction. The default policy retries the whole action once after 100 ms - see
[Actions](actions.md#retries-on-optimistic-lock-conflicts).

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

## Generated builders

`wallet.copy().balance(...)` works with nothing generated. What it cannot do is tell a *type
checker* that `.balance(...)` takes a `Decimal` — setters resolve by name at runtime, so a
`.balance("25.50")` is a string in a `NUMERIC` column that nothing complains about until the
database does.

That is the one thing Python cannot work out for itself, and the one thing the generator exists
for:

```
uv run python -m ekbatan.codegen myapp.model --out myapp/builders.py
```

It imports the modules you name, finds every `Model` and `Entity` in them, and writes a builder
per class. For a `Wallet` with `owner_id: UUID`, `currency: str` and `balance: Decimal`:

```python
class WalletBuilder(ModelBuilder[Wallet, WalletId, WalletState]):
    """Builds a Wallet, with the field types a checker can see."""

    builds = Wallet

    def owner_id(self, value: UUID) -> Self:
        return self.set(owner_id=value)

    def currency(self, value: str) -> Self:
        return self.set(currency=value)

    def balance(self, value: Decimal) -> Self:
        return self.set(balance=value)


def wallet(source: Wallet | None = None) -> WalletBuilder:
    """A fresh WalletBuilder, optionally pre-loaded from an existing Wallet."""
    made = WalletBuilder()
    return made.copy_base(source) if source is not None else made
```

**What is generated:**

- A class named `<ModelName>Builder`, subclassing `ModelBuilder` (or `EntityBuilder`) with the
  right type parameters.
- A `builds` attribute naming the class it constructs — which is what `build()` calls, so there
  is no `build()` override to generate.
- One fluent setter per field the model **declared**, returning `Self` so the chain keeps its
  type. `id`, `state`, `version`, `created_date` and `updated_date` are typed on the base and are
  not repeated.
- A module-level factory named after the model in snake case — `wallet()` for `Wallet`,
  `wallet_account()` for `WalletAccount` — optionally pre-loaded from an existing instance.
- The imports the file needs, emitted relative when `--out` lands inside your package.

**What is not generated** — these stay on the model, where you can see them:

- The dataclass itself and its field declarations.
- Validation. `deposit()` raising on a non-positive amount is business logic, not boilerplate.
- Business methods, and the events they attach.
- Factory methods like `Wallet.open(...)` that set up a fresh aggregate and its first event.
- The one place your model names it — its own class line. The generator writes a separate file
  and never edits your model:

  ```python
  class Wallet(Model[WalletId, WalletState, WalletBuilder]):
      balance: Decimal
  ```
- Any conditional default or computed field. If a builder needs one, write that builder by hand;
  the generator will not fight you for the name.

**Commit the output**, and **regenerate it when a model changes.** It is derived from source
you already have, unlike the schema module, which is derived from a database you have to start.

Regenerating is not optional discipline, and it is worth knowing exactly what a stale builder
costs, because it is less than you would hope: a setter the builder does not declare is **not**
caught by a type checker — the base resolves setters through `__getattr__`, which accepts any
name — and `build()` fails at runtime only if the new field is *required*, taking the default
silently if it is not.

Java has no equivalent exposure: its annotation processor runs on every compile, so its builder
cannot be stale or missing. `scripts/check-builders.py` restores that guarantee here — it
regenerates into memory, compares, and fails `make lint` on a builder that is missing or behind
its model. A builder without the generator's header is taken as hand-written, and only its
existence is checked.

**A model must name a typed builder**, on its class line:

```python
class Wallet(Model[WalletId, WalletState, WalletBuilder]):
    balance: Decimal
```

That third argument is the return type of `copy()` and `builder()`, so a checker reads the real
setters. Leave it out and both refuse:

```
Wallet.copy() needs a typed builder, and Wallet has not named one.
  generated   python -m ekbatan.codegen myapp.model --out <pkg>/builders.py
  by hand     class WalletBuilder(ModelBuilder["Wallet", ...]): ...
then name it on Wallet's class line, which is the whole of the wiring.
```

Java's `copy()` returns a generated builder or the class does not compile. There is no compile
step here, so the refusal is **per route and at the call** — a model that only ever calls
`copy()` needs only that override, and an entity built solely through a factory like
`Alert.create(...)` and never copied needs neither.

**Generated or hand-written makes no difference** — what is required is a builder a checker can
read, and writing one by hand is better where a setter needs a conditional default or a computed
field. `ModelBuilder(X)` is still reachable by name, for a model defined inside a test with
nowhere to put a builder. What is closed is reaching the untyped builder *through a model*.

`evolve()` is unaffected, and is still the right call for a one-field change.

## Inherited builder methods

Both builders carry a shared set on the base, typed `Self` so the return stays your builder:

| | model | entity |
| --- | :-: | :-: |
| `id(...)` | yes | yes |
| `state(...)` | yes | yes |
| `version(...)` / `with_initial_version()` / `increase_version()` | yes | yes |
| `copy_base(other)` | yes | yes |
| `set(**changes)` | yes | yes |
| `with_event(...)` / `events(...)` | yes | — |
| `created_date(...)` / `updated_date(...)` | yes | — |

`copy_base(other)` is what `model.copy()` returns first: the id, state, version, and for a model
the events and timestamps, pre-populated — so domain code only names the fields that changed.

The split is the whole difference between the two kinds. An entity has no events to attach and
no timestamps to hold, so those methods are not on it to be called by mistake.

## Naming conventions

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
