# Models and entities

Two kinds of persistable object. Both immutable, both version-tracked, both with a `state` column
that supports soft deletion. The difference is whether their changes are news. They are the same
classes in both modes, sync and async: nothing on a model or an entity waits on the database, so
every sample on this page is the same either way.

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
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def validate(self) -> None:
        if self.balance < 0:
            raise ValidationError("balance cannot be negative")

    @staticmethod
    def create_wallet(
        shard: ShardIdentifier,
        owner_id: UUID,
        currency: str,
        initial_balance: Decimal,
        created_date: Instant,
    ) -> WalletBuilder:
        wallet_id = ShardedId[Wallet].generate(shard)
        return (
            WalletBuilder()
            .id(wallet_id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(initial_balance)
            .created_date(created_date)
            .with_initial_version()
            .with_event(WalletCreatedEvent(model_id=str(wallet_id), owner_id=owner_id,
                                          currency=currency, initial_balance=initial_balance))
        )

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError("Deposit amount must be positive")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(WalletMoneyDepositedEvent(model_id=str(self.id), amount=amount, new_balance=new_balance))
            .balance(new_balance)
            .build()
        )

    def close(self) -> Wallet:
        if self.state is WalletState.CLOSED:
            return self
        return self.copy().with_event(WalletClosedEvent(model_id=str(self.id))).state(WalletState.CLOSED).build()
```

Three patterns worth naming:

- **`validate()`** holds the wallet's own rules, and runs on every wallet made - see
  [below](#validate-the-models-own-rules).
- **A factory** (`create_wallet`) starts a *new* aggregate: its builder, with
  `with_initial_version()` and the creation event attached. The action that calls it builds it.
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

`kw_only` is doing two jobs, and the second is not optional. It stops transposition, because
`owner_id` sits next to `currency` and `started_date` next to `completion_date` - adjacent
same-typed fields that swap silently when passed positionally. It also makes the inheritance
legal at all: the base carries `updated_date` and `events` with defaults, and a subclass
field without one would otherwise be *"non-default argument follows default argument"*, a
`TypeError` at class-definition time. Keyword arguments have no order, so that cannot arise.

### `copy()` versus `evolve()`

`evolve()` is the framework's own shorthand and exists because internal code needs it. Domain
code should prefer `copy()`, so that every change in an application reads the same way.

## Equality

The same row at the same version is the same object:

| | equal when these match | hash |
| --- | --- | --- |
| `Model` | class, `id`, `state`, `version`, `created_date`, `updated_date` | `hash(id)` |
| `Entity` | class, `id`, `state`, `version` | `hash(id)` |

Business fields and events take no part, so two copies of one wallet at one version are equal
whatever each says the balance is - and any model can go in a set or be a dict key, list and dict
fields included. It is how Python compares saved records too (Django by primary key, SQLAlchemy by
one object per row); comparing every field is for value objects.

An event is such a value object: two are equal when they are the same class, about the same model
(`model_id`), with the same fields. The same deposit into two different wallets is two events,
not one.

`@dataclass(frozen=True)` on a model would write a whole-row `==` of its own, so the framework
gives each model this one as it is defined. A model that writes its own `__eq__` keeps it.

## ModelEvent

A `ModelEvent` is a frozen dataclass carrying whatever the domain needs:

```python
@dataclass(frozen=True, kw_only=True)
class WalletMoneyDepositedEvent(ModelEvent["Wallet"]):
    amount: Decimal
    new_balance: Decimal
```

The class line names the model the event is about, and the constructor takes that model's id -
`WalletMoneyDepositedEvent(model_id=str(self.id), amount=..., new_balance=...)`. The type argument
fixes `model_name` once per class, so no call site passes it. Those two fill the outbox's `model_id` and `model_type` columns. A missing or blank id
is refused when the event is made.

**There is no deserialisation boilerplate.** The payload is rebuilt from the dataclass's own
annotations - a `Decimal` from its number, an `Instant` from its ISO form, an enum from its name.
Nothing to write, and nothing to forget. See [The outbox](outbox.md) for the row the event
lands in.

### Deserialization (for in-process handlers)

Events are stored as JSON in `events.payload` and read back into the same class when an
in-process handler is invoked. **Nothing extra is needed for that round trip** — no annotation,
no registered constructor, no per-handler mapping layer. The payload is rebuilt from the
dataclass's own annotations, which is the one place the field types are already written down,
and its `__init__` already takes keyword arguments named after its fields.

What the decoder does with each field, driven by its annotation:

| Annotation | Written as | Read back as |
|---|---|---|
| `Decimal` | a JSON **number**: `10.50`, `1E+3` | the same `Decimal`, scale kept |
| `float` | a number: `0.1`, `1.0E20` | `float` |
| `Instant` (`ekbatan.time`) - no zone attached, UTC digits | ISO-8601 text ending in `Z`: `"2025-01-01T00:00:00.120Z"` | no zone attached, the same digits |
| `LocalDateTime` - no zone attached | ISO-8601 text, no offset: `"2025-01-01T10:00:00"` | no zone attached, as written |
| `OffsetDateTime` - zone attached | ISO-8601 text with the offset: `"...T02:00:00+02:00"` | zone attached, that offset |
| plain `datetime` | **refused** - declare it one of the three | |
| `date`, `time`, `timedelta` | ISO-8601 text: `"2025-01-01"`, `"10:00:00"`, `"PT1.5S"` | the same type |
| `bytes` | base64: `"AQL/"` | `bytes` |
| `UUID` | its text | `UUID` |
| an `Enum` | its **member name**, never its ordinal | the member |
| `Id`, `ShardedId` | `{"id": "...", "value": "..."}` | the typed id |
| another `TypedValue` | `{"value": ...}` | the typed value |
| a nested frozen dataclass | an object, keys as below | recursively, the same way |
| `list` / `tuple` / `set` / `frozenset` / `dict` of any of the above | array / object | element by element |
| `T \| None` | | the non-`None` arm, when there is exactly one |

A `datetime` field says which kind of time it holds: `Instant`, `LocalDateTime` or
`OffsetDateTime`, from `ekbatan.time` ([time kinds](../database/multi-database.md#time-kinds)). Python has one class for the three,
and only the declaration tells a reader which it gets. A field declared plain `datetime`, or a
value of the wrong sort for its kind - a zone attached in an `Instant` field - is refused when
the event is written, inside `executor.execute`: the action fails and nothing is saved.

**Anything else is refused** with a `ValidationError` naming its class, before anything is
written: a plain class or an `attrs` class, for example (a `NamedTuple` is a tuple, so it is an
array). Make it a dataclass to have it written and read back.

**Keys are camelCase**: `new_balance` is written `newBalance`, in the order the fields are
declared. A `dict` keeps its own keys, in insertion order; nothing is sorted. The camelCase keys
and the value forms above are the JSON a Java service built on Ekbatan reads and writes, so the
two can handle each other's events - see
[Running beside a Java service](../beside-java.md#event-payloads).

**Every JSON number is read as a `Decimal`**, so nothing is lost on the way in. One database is
the exception: MySQL's `JSON` type stores a number with a fraction as a double, so `50.00` comes
back `50.0` there.

Enums travel by name - an ordinal changes meaning the moment somebody inserts a member in the
middle, and nothing about that change looks dangerous at the time.

Two limits worth knowing:

- **A key the dataclass does not declare is dropped**, not an error. An additive change on the
  producer cannot break a consumer that has not caught up — which is the same tolerance the
  streaming path has, and for the same reason. **A key the JSON lacks leaves the field `None`**
  (or its declared default).
- **A union with more than one non-`None` arm is left as raw JSON.** There is no discriminator in
  the payload to pick an arm with, so the decoder does not guess. Model that as a nested
  dataclass with a `kind` field rather than as a union if a handler needs it typed.

Every payload starts with `modelId` and `modelName` - the event's `model_id` and its model's
class name - so a reader of the raw JSON knows what it is about; a Java consumer requires them
there. `modelName` is not read back: it comes off the class line.

Streaming-only consumers do not go through any of this: they read the JSON off the broker.

`created_date` is **required**: `build()` refuses a model without it. A model records
something that happened, and when it happened is part of the record rather than a column the
framework backfills afterwards. Pass `self.clock.instant()` from the action, so a test with a fixed
clock gets a fixed date:

```python
Wallet.create_wallet(shard, owner_id, currency, initial_balance, self.clock.instant())
```

`updated_date` falls back to `created_date` — a row that has never changed was last updated when
it was created. Both are kept exactly as given - no zone added, removed or moved - and stored
that way ([What you give is what is stored](../database/multi-database.md#what-you-give-is-what-is-stored)). Both are typed `Instant` ([time kinds](../database/multi-database.md#time-kinds)): from `clock.instant()` they are UTC
with no zone attached, so `wallet.created_date` compares with the
clock and equals the value read back from any database.

**Nothing moves `updated_date` but you.** Neither the plan nor the repository touches it on a
write. Move it on the changes where it means something:

```python
def deposit(self, amount: Decimal, at: datetime) -> Wallet:
    return (
        self.copy()
        .with_event(WalletMoneyDepositedEvent(model_id=str(self.id), amount=amount, new_balance=self.balance + amount))
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

`Entity` has neither. Something that needs to know when it last changed almost
certainly has changes worth telling the rest of the system about, which makes it a `Model`.

## Required, and optional

The base refuses `None` for any field whose annotation does not admit it — **including the ones
you declare**. `owner_id: UUID` is required; `note: str | None` is not.

| field | `None` |
|---|---|
| `currency: str` | refused |
| `note: str \| None` | accepted |

The base reads the rule off the dataclass's own annotations, so there is no null check to write
in each model, and none to forget for a field added later. Every other rule goes in
`validate()`.

The annotations have to be readable while the program runs. A type imported only under
`if TYPE_CHECKING:` is not, and creating such an object is refused with a `TypeError` that
names it, rather than leaving the class's fields unchecked.

## `validate()`: the model's own rules

Every model and every entity defines `validate()`. It is the one place for the rules a model has
beyond its field types:

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def validate(self) -> None:
        if self.balance < 0:
            raise ValidationError("balance cannot be negative")
```

**It runs every time a model is made**: `build()`, `copy()...build()`, the constructor,
`evolve()`, and `from_record` when a repository reads a row. So no path hands back a wallet with
a negative balance - not a deposit, not a withdrawal, not a row read from the database.

**Ekbatan's own checks run first.** By the time `validate()` runs, every field whose type does not
allow `None` is set, `version` is at least 1, and on a model `updated_date` has fallen back to
`created_date` and `events` is a tuple. So `validate()` only says what the types cannot; it never
checks a `Decimal` field for `None`.

**It is required.** A model or entity without it is refused when its class is defined - at import,
not at the first row:

```
TypeError: Wallet must define validate(); write `pass` if it has no rules beyond its types
```

and mypy and pyright flag the line that makes one (`Cannot instantiate abstract class "Wallet"`).
A model with no rules of its own says so:

```python
    def validate(self) -> None:
        pass  # no rules beyond its types
```

**Raise `ValidationError`** (from `ekbatan.errors`). That is the convention, and what ekbatan's own
checks raise. Any other exception works too; it reaches whoever made the row.

**A rule applies to stored rows as well.** Reading a row runs `validate()`, so a rule added today
refuses an old row that breaks it, where that row is read. Fix the stored rows before you ship a
stricter rule.

**A base class of your own**, one that real models inherit from, lists `ABC` to say it is not a
model itself. The rule then applies to each model that inherits from it:

```python
@dataclass(frozen=True, kw_only=True)
class AuditedModel[ID, S: Enum](Model[ID, S], ABC):
    audited_by: str
```

A base that still has abstract methods of its own, besides `validate()`, is let through too.

**Your own `__post_init__` is safe.** If a model defines one, ekbatan wraps it when the class is
defined: its own checks first, then your `__post_init__`, then `validate()` - whether or not you
call `super().__post_init__()`. Calling it does no harm and nothing runs twice. Keep rules in
`validate()`; `__post_init__` is for work such as tidying a field.

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
class WalletBuilder(ModelBuilder[Wallet, ShardedId[Wallet], WalletState]):
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
- Validation. `validate()`, and `deposit()` raising on a non-positive amount, are business
  logic, not boilerplate.
- Business methods, and the events they attach.
- Factory methods like `Wallet.create_wallet(...)` that set up a fresh aggregate and its first event.
- The one place your model names it — its own class line. The generator writes a separate file
  and never edits your model:

  ```python
  class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
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

So check it rather than remember it. This repository's `scripts/check-builders.py` does — it
regenerates into memory, compares, and fails `make lint` on a builder that is missing or behind
its model; a project of your own can run the generator the same way in CI. A builder without the generator's header is taken as hand-written, and only its
existence is checked.

**A model must name a typed builder**, on its class line:

```python
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
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

There is no compile step to catch a missing builder, so the refusal is **per route and at the
call** — a model that only ever calls
`copy()` needs only that override, and an entity built solely through a factory like
`Notification.create_notification(...)` and never copied needs neither.

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
  happened: `WalletCreatedEvent`, `WalletMoneyDepositedEvent`, `TransferFailedEvent`.
- **Action** - imperative, because an action describes intent: `WalletCreateAction`,
  `WalletDepositMoneyAction`.

- **Id** - no class of its own: `ShardedId[Wallet]` for a model whose rows are routed by id,
  `Id[Notification]` otherwise. A type checker keeps two models' ids apart; at runtime they compare by
  uuid.

An action is named for what it does to what: the wallet's own actions put the model first -
`WalletCreateAction`, `WalletDepositMoneyAction`, `WalletCloseAction` - so a model's actions sort
together, and a step of a process names the step - `InitiateTransferAction`.

## See also

- [Actions](actions.md) - how these flow through `perform()` into the database
- [The outbox](outbox.md) - where the events go
- [Getting started](../getting-started.md) - the repository that maps a model to a row
