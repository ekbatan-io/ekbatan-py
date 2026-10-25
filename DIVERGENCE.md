# Where this port disagrees with the charter's reference

Most of what follows is neutral: the two ports do the same thing in different idioms.
For the biased subset - every way this port is *behind* the Java one, in one place -
see [`docs/SHORTCOMINGS.md`](docs/SHORTCOMINGS.md).

Rule 1 of [the charter](https://github.com/ekbatan-io/ekbatan-spec) is that idiomatic beats
identical: a port must read as if written by someone fluent in the language, not translated
into it. That guarantees disagreements, and written disagreement is how a port stays honest -
silent divergence is how a spec becomes fiction.

Every entry below is a place where this port does something the Java implementation does not.
None of them changes observable behaviour; each one changes what the application has to write.

## How to read this

Every section below is labelled on the line under its heading:

| | |
| --- | --- |
| **forced** | CPython or its libraries left no choice. Nothing to review - the alternative does not exist |
| **chosen** | somebody decided, and it could have gone the other way. Links [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md), which lists all of them together |
| **parity** | recorded here because the mechanism differs, while the behaviour does not |

`uv run python scripts/divergence.py` refuses to pass if a section is unlabelled, or if a
**chosen** one does not link somewhere it will be argued about. It runs in `make lint`.

The labels exist because of a specific failure. `readonly_db()` was made to mean "a read,
defaulting to the primary" rather than Java's "the replica" - a judgement call about whose job
consistency is - and it was written up here as a neutral difference. It read like a fact about
the language for weeks, and was caught by being asked about rather than by being reviewed.

## An action reaches its three per-invocation values exactly as Java does

> **parity** - clock in the constructor, principal as a parameter, plan ambient. The same three mechanisms Java uses, for the same three values.

This was briefly not true, and the way it was found is the point: it was asked about rather than
reviewed - the same failure the labels at the top of this file exist because of.

For a while all three lived in `ContextVar`s, reached as `self.plan`, `self.principal` and
`self.clock`, with `current_principal()` and `current_clock()` alongside. That is more
*internally* consistent than Java, which uses a different mechanism for each - and it was the
wrong trade, because the shape a reader recognises is worth more here than a tidier one.

Now:

| | Java | here |
|---|---|---|
| clock | `Action(Clock clock)`, a `public final` field | `super().__init__(clock)`, a field |
| principal | `perform(Principal principal, PARAM params)` | `perform(self, principal, params)` |
| plan | `ScopedValue` -> `plan()` | `ContextVar` -> `self.plan` |

The plan stays ambient in both, and for the same reason: a repository call three frames down has
to stage onto it without mentioning it. The other two can be passed, so they are.

One difference remains, and it is forced. The principal is a `str | None` rather than a
`java.security.Principal`, because there is no such interface here and inventing one to hold a
name would be a type for its own sake.

**The clock is not checked, as in Java** (`EKB-0302`). This port used to refuse an executor whose
actions held a different clock object than its own - and so refused two `SystemClock()` objects,
a trap with no Java counterpart (Dracula 12). Java's executor never compares them: the action
reads its own clock in `perform`, and the rows are stamped from the executor's. Pass the same
clock to both if you want a frozen test to freeze both.

## An action runs by its type, as Java's runs by its class

> **parity** - the executor takes a type and runs the instance registered for it; an instance is refused. Brought to parity in `EKB-0224`; until then an instance was accepted too.

```java
executor.execute(principal, WalletDepositMoneyAction.class, params);
```

```python
executor.execute(WalletDepositMoneyAction, WalletDepositMoneyAction.Params(...), principal="alice")
```

The registry is the only way in, in both ports: a caller holding the executor runs an action
without holding what it was built with, and everything a service can do is listed in one place.

This port used to accept an instance as well, for tests. Measured before removing it, that form
ran an action that had never been registered. An instance is refused
now: an instance gets a message saying to pass the type, and to use `ActionSpec.of(action)` for
the test that wanted it, which is what Java's tests use. The framework's own executor tests do
what Java's `ActionExecutorTest` does - register the case's action on an executor, then run it by
class (`fixtures.executing.run`).

Two differences inside the builders, both for the same reason. Neither `with_action` nor
`with_model_repository` takes the type alongside the object - `type(action)` and
`repo.model_type` already say it, and a second way to say the same thing is a second way for it
to be wrong. And where Java's `repository(Class)` returns null for an unregistered type, this
raises and names the type: the null is what surfaced as a NullPointerException inside the
executor's shard grouping, which is `EKB-0076` on the Java side.

## An event says which model it is about, as Java's does

> **parity** - the class line names the model and the constructor takes its id, as Java's `super(id, Wallet.class)` does. Brought to parity in `EKB-0218`; until then the persister filled both in from the model the event was attached to.

Java:

```java
public class WalletMoneyDepositedEvent extends ModelEvent<Wallet> {
    public WalletMoneyDepositedEvent(ShardedId<Wallet> walletId, BigDecimal amount, BigDecimal newBalance) {
        super(walletId.getValue().toString(), Wallet.class);
        ...
```

This port:

```python
@dataclass(frozen=True, kw_only=True)
class WalletMoneyDeposited(ModelEvent["Wallet"]):
    amount: Decimal
    new_balance: Decimal

WalletMoneyDeposited(model_id=str(self.id), amount=amount, new_balance=new_balance)
```

The type argument does what `Wallet.class` does: it fixes `model_name` once per event class, so
no call site passes it, and `model_name` is not a constructor argument at all. It is a string
because events are declared before the model that emits them - the same reason `ShardedId["Wallet"]`
is one. Both refusals are Java's: a missing or blank `model_id` (`Validate.notBlank`), and an
event class that names no model (Java's `modelClass cannot be null`, which there cannot compile).

The persister writes `event.model_id` and `event.model_name` straight to their columns, as
`SingleTableJsonEventPersister` writes `event.modelId` and `event.modelName`. It does not look at
the model the event was attached to.

Two differences in mechanism, neither in behaviour:

- Java's constructor parameter is typed `ShardedId<Wallet>` and stringified inside; here the
  field is Java's `String modelId` and the caller passes `str(self.id)`. The stored value is the
  same string.
- Deserialising, Java's `@JsonCreator` takes `modelName` and ignores it for `Wallet.class`. This
  port's decoder skips it for the same reason: it is not a constructor argument.

The payload carries both under Java's names, `modelId` and `modelName`, so either port can read a
row the other wrote.

## The JSON of params and payloads is Java's, byte for byte

> **parity** - since `EKB-0290` the outbox's `action_params` and `payload` are the text Ekbatan's default Jackson 3 mapper writes for the same object. Two corners Python cannot reach are listed below and in [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md#json-domain-model-and-timestamps-ekb-0290-ekb-0295).

A dataclass field `new_balance` is written `newBalance`, in declaration order (a Java record, or
an event with Ekbatan's usual `@JsonCreator`); decimals are JSON numbers in `BigDecimal.toString()`
form; floats in `Double.toString()` form; a `datetime` is written as the kind its field declares
(`ekbatan.time`: `Instant`, `LocalDateTime`, `OffsetDateTime` - see [time
kinds](#time-kinds-are-labels-on-one-datetime-a-json-time-field-must-declare-one)); `date`, `time`, `timedelta` are `LocalDate`,
`LocalTime`, `Duration`; bytes are base64; `Id` and `ShardedId` are `{"id": ..., "value": ...}`
and any other `TypedValue` `{"value": ...}`, which is what Jackson makes of Java's getters. Checked
against Jackson over 50,000 values (`parity-probes/G-domain-model/JsonFormatsParityProbeTest.java`
and `json_formats_probe.py`): every one the same.

Reading parses every JSON number as a `Decimal`, so Python loses nothing - including from Java's
rows, where Java's own reader loses digits (it reads numbers through a `double`; noted for the
Java side in DECISIONS-TO-REVIEW). Each field is rebuilt as its declared type.

What Python cannot match:

- **A Java class with no `@JsonCreator`** has its keys sorted by Jackson 3. A dataclass always
  has a constructor, so its keys follow declaration order, as a record's or a creator's do.
- **A missing key for a Java primitive** (`long`, `double`, `boolean`) is refused by Jackson 3;
  Python has no primitive types, so a missing key is `None`, as for Java's object types.

## A typed builder is required in both ports, for different reasons

> **chosen** - both ports require a builder a checker can read; what the *generation* buys differs, and accepting a hand-written one is an addition. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

This section used to say "no builder is generated", which was true when it was written on
3 September and false on 4 September, when `ekbatan.codegen` landed. It went a year unread. The
labels at the top of this file exist because of one entry that rotted quietly; this is the
second, and it was caught the same way - by being asked about.

Both ports generate a builder. The difference is what each generator is *for*:

| | Java | here |
|---|---|---|
| construct, copy, `with_event`, version | generated by the annotation processor | already there, resolved from the dataclass at call time |
| field types a checker can see | generated | generated by `ekbatan.codegen` |
| **delete the generated file** | **does not compile** | **still runs** |

`EntityBuilder` and `ModelBuilder` resolve a setter by name and refuse an unknown field at the
call:

```python
wallet.copy().balance(new_balance).with_event(MoneyDeposited(...)).build()
```

That works for every model there will ever be, with no build step. What it cannot do is tell a
*type checker* that `.balance(...)` takes a `Decimal` - the setters arrive through `__getattr__`,
and a checker sees only that. So `codegen` writes the signatures down, and nothing else:

```python
def balance(self, value: Decimal) -> Self:
    return self.set(balance=value)
```

The body is the same `set()` the runtime builder already uses. Java's processor is load-bearing;
this one is a convenience you can throw away, losing the checking rather than the behaviour.

`evolve()` is the short form for a change touching one or two fields, and is what the reference
application uses:

```python
return self.evolve(balance=new_balance, emit=MoneyDeposited(...))
```

### What the committed file costs, and the guard for it

Java's processor runs on every compile, so its builder cannot be stale or missing. Here the file
is generated once and committed - friendlier, since there is no build step and the output is
readable - and that leaves two failures Java does not have:

- **Missing.** `copy()` used to fall back to the runtime builder and nothing complained - the
  checking the file existed to provide was quietly gone. Closed by the first guard below.
- **Stale.** A model gains a field and the builder does not. The setter call is **not** caught -
  `__getattr__` accepts any name - and `build()` then fails at runtime if the new field is
  required, or silently takes the default if it is not.

`codegen`'s own docstring claimed a stale builder was "a type error rather than a silent wrong
answer". Measured: it is a *runtime* error, and only for a required field.

Two guards restore it, because a lint check a developer never runs is not a guarantee:

**`copy()` and `builder()` refuse** unless the model overrides them (`EKB-0196`). Java's
`copy()` returns a generated builder or the class does not compile; there is no compile step
here, so the refusal is at the call, with the message naming both ways to satisfy it:

```
Wallet.copy() needs a typed builder, and Wallet has not overridden it.
  generated   python -m ekbatan.codegen myapp.model --out <pkg>/builders.py
  by hand     class WalletBuilder(ModelBuilder[Wallet, ...]): ...
```

**Generated or hand-written makes no difference.** What is required is a builder a checker can
read; a hand-written one does that as well, and better where a setter needs a conditional
default. `ModelBuilder(X)` stays reachable by name - a model defined inside a test has nowhere
to put a builder, and the framework's own internals use it - so the route that is closed is
reaching the untyped builder *through a model*, which is the route an application takes.

**`scripts/check-builders.py`** catches the other half, in `make lint`: a builder that exists
but is behind its model. It regenerates into memory and compares. A file without the
generator's header is taken as hand-written and only its existence is checked.

`evolve()` is unaffected and is still the right call for a one-field change - it goes through
`dataclasses.replace`, not the builder.

## `copy_base` takes every field, not only the framework's

> **forced** - A Java base class cannot enumerate a subclass's fields; a dataclass can. The behaviour follows from the language. **Settled, and nothing is owed**: this port's side is the better one and is staying. "Closing" it would mean typing your field names a second time so they could get out of step.

The name is Java's. What it copies is not: Java's `copyBase` hands over the six fields the base
class knows about and leaves the subclass's for the next lines, because a Java base class
cannot enumerate a subclass's fields. That is why every Java model writes its own `copy()`, and
why one of them eventually forgets a field.

Here the fields come off the dataclass, so `copy_base` takes all of them and the base can
write `copy()` for you. A model that gains a field gains it everywhere at once - there is no
second list to keep in step, which is the whole of the difference.

What the model still owes is the *type*: which builder `copy()` hands back. Java needs no such
declaration because its `copy()` is `abstract` - every model writes the method out, and writing
it is where the narrower return type goes. With the method gone, the type has nowhere left to
live but the class line (`EKB-0202`):

```python
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
```

Leave it off and `copy()` refuses, for the reason the section on the builder gives. Override
`copy()` only to make a copy that deliberately differs - dropping a cache, resetting a counter.

## Identifiers are spelled as Java's: `ShardedId[Wallet]`, and a `ShardedId` is not an `Id`

> **parity** - the model is the type argument and there is no class per model, as Java's `Id` and `ShardedId` are `final`; and `ShardedId` stands beside `Id` rather than under it. Brought to parity in `EKB-0223`; until then this port gave each model a named subclass and made `ShardedId` an `Id`.

```java
ShardedId<Wallet> id = ShardedId.generate(Wallet.class, shard);
```

```python
id: ShardedId[Wallet] = ShardedId[Wallet].generate(shard)
```

The subscript does what Java's class argument does, and no more: it tells the checker which model
the id is for. Java keeps nothing - `ShardedId.of(Wallet.class, u)` null-checks the class and
discards it - so at runtime two models' ids holding one uuid are **equal**, measured on Java's own
classes through `jshell`. They are here too. What keeps them apart is the checker: mypy refuses
`ShardedId[Notification]` where `ShardedId[Wallet]` is expected, as javac refuses it, and a bare `UUID`
likewise.

A named subclass per model (`class WalletId(ShardedId["Wallet"])`) was this port's convention.
It made two models' ids unequal at runtime, which is stricter than Java - but nothing in the
framework relied on it, and it did not catch the mistake it looks like it would: the wrong
model's id handed to a repository still ran the query and reported "not found", measured. It is
gone; so is the test that advertised it.

**A `ShardedId` is not an `Id`.** Java's `ShardedId` implements `ShardAwareId` and `ModelId` and
extends neither `Id` nor `TypedValue`; `Id.equals` compares `getClass()` and `ShardedId.equals`
requires another `ShardedId`, so an `Id` and a `ShardedId` of one uuid never compare equal.
Measured the same way. This port had `ShardedId` subclass `Id` and equality span the two; it now
matches. Nothing depended on the subclassing - routing reads the uuid off whatever id it is given.

Mechanism, not behaviour:

- **The model's own class line quotes it**: `class Wallet(Model[ShardedId["Wallet"], ...])`,
  because the class does not exist yet there. Python never evaluates a type argument on a class
  line, so the framework resolves that reference itself when it reads a model's types; left
  unresolved, the builder generator wrote `ShardedId[Any]`, accepting every model's id.
- **`isinstance(x, ShardedId[Wallet])` raises**, as Java refuses `instanceof ShardedId<Wallet>`.
  `isinstance(x, ShardedId)` is the check both languages allow.
- **Here `ShardedId` is still a `TypedValue`**, which is how the repository knows to unwrap a
  model's id for its column - and to refuse one handed to a read by id on a plain uuid column,
  which takes the raw value as Java's does (`EKB-0541`). Its equality keeps it apart from `Id` all
  the same.
- **`ShardedId.shard` refuses a uuid that carries no shard.** Java reads shard bits out of any
  uuid, a random version 4 one included, and routes it to a shard nobody chose - `(247, 28)` in
  the run that checked it. Listed under "Noticed in the Java".

## The repository classes are Java's four: `Repository`, `AbstractRepository`, `ModelRepository`, `EntityRepository`

> **parity** - Java's names and layering, with the narrow `Repository` promise as a `Protocol` and `AbstractRepository` abstract. Brought to parity in `EKB-0226`; until then this port had no narrow interface and called the implementation `Repository`.

```java
public interface Repository<PERSISTABLE extends Persistable<?>>
public abstract class AbstractRepository<...> implements Repository<PERSISTABLE>
```

```python
class Repository[M: Persistable[Any, Any]](Protocol): ...
class AbstractRepository[M: Persistable[Any, Any], R: Mapping[str, Any], K](Repository[M], ABC): ...
```

`Repository` is the promise: what the registry, the executor and the change persister ask of a
repository, and all they ask. They depend on it and on nothing else, so anything that keeps it
can be registered - as anything implementing Java's interface can. `AbstractRepository` keeps it
against a table, and is abstract for Java's reason: `to_record` and `from_record` are the
repository's to write. An application extends `ModelRepository` or `EntityRepository`, as in Java.
The type parameters follow Java's order - model, record, id - less the table, which here is a
`Table` value rather than a generated type: `AbstractRepository<Wallet, WalletsRecord, Wallets,
UUID>` there, `ModelRepository[Wallet, WalletsRecord, UUID]` here. The key is what the id column
holds in both - a `UUID`, so a read by id takes `wallet.id.value` as Java's takes
`wallet.id.getValue()` - and a forced type that converts the id column changes it in both, as a
jOOQ forced type changes `DB_ID`. A key of another type is refused (`EKB-0541`; until then this
port's key was the typed id, unwrapped).

The reason this port had for merging the two - that its `Repository` "works as it stands",
mapping by field name - stopped being true in `EKB-0217`, when the mapping became something
every repository writes.

Mechanism, not behaviour:

- **The promise is a `Protocol`** - Python's interface. Java's ten methods in snake case,
  plus three the plumbing here reads where Java's reads something
  else: `model_type` (this registry learns the type from the repository), `sharding` (Java's
  `shardingStrategy()`) and `shard_of` (Java's executor asks the strategy directly).
- **A repository without its mapping cannot be created**, where Java's does not compile - the
  nearest a language without a compile step comes. The message names what is missing and shows
  the two lines to write.
- **`Repository[Wallet, WalletId]`, the old two-argument base, says what moved**, rather than
  Python's bare "too many arguments" - naming the three-argument classes that implement it.

## Row mapping is written, as Java's is - but the bridge is not

> **parity** - both ports require every repository to write the mapping. Brought to parity in `EKB-0217`; it defaulted to field name until then, which was the one piece of magic left in the read and write paths.

Java declares both halves abstract, so a repository that has not written them does not compile:

```java
public abstract PERSISTABLE fromRecord(RECORD record);
public abstract RECORD toRecord(PERSISTABLE domainObject);
```

`to_record` and `from_record` (`to_row` / `from_row` until `EKB-0409`) refuse instead, there
being no compile step here. Every repository in this repository writes both - the examples, the
suite's fixtures, and the handful of tests that build one inline. And since `EKB-0409` both are
typed by the table's record, as Java's are by `WalletsRecord`: `to_record(wallet) ->
WalletsRecord`, `from_record(record: WalletsRecord) -> Wallet`, so a column left out, misspelt or
mistyped is a mypy error where Java's is a compile error.

**Why it changed.** The mapping used to default to field name, and the entry here called that
"this port's largest ergonomic decision". It was also the one place left where something
happened that nobody had written: rename a column without renaming the field and the mapping
silently stopped matching, where Java gets a compile error. A default that is right almost
always is the hardest kind of wrong to find.

**What is still not written is the bridge.** Where the names line up - which is most of the
time - the two methods are one line each:

```python
def to_record(self, wallet: Wallet) -> WalletsRecord:
    return self.columns_by_field_name(wallet)
```

Java lists every column twice because jOOQ hands it a *generated record type* and something has
to carry values across. A dataclass knows its own fields, so `columns_by_field_name` is written
once in the framework rather than once per repository. **Choosing the mapping is yours;
implementing it is not** - which is the same split as `copy_base`, and for the same reason.

So the divergence that remains is small and is in the other direction: Java writes N lines per
repository where this writes two, and neither port guesses.


## `created_date` is required, as Java requires it

> **parity** - Java's constructor says `Validate.notNull(builder.createdDate)` and `build()` throws without it. So does this. Brought to parity in `EKB-0192`.

Java's `Model` constructor:

```java
this.createdDate = Validate.notNull(builder.createdDate, "createdDate cannot be null")
        .truncatedTo(MICROS);
this.updatedDate = builder.updatedDate != null ? builder.updatedDate.truncatedTo(MICROS)
                                               : this.createdDate;
```

and its factories pass `clock.instant()` in. This port now does the same three things:
`created_date` is required and refused when null, `updated_date` falls back to it, and both are
kept **exactly as given** - see the next entry, [time is stored as
given](#time-is-stored-as-given-the-default-clock-gives-naive-utc). `datetime` holds
microseconds only, so `truncatedTo(MICROS)` has nothing to do.

For a while this port left both unset and stamped them from the executor's clock when the change
was staged, on the grounds that two lines which are the same every time and wrong occasionally do
not belong in application code. That is a fair argument for a **default**; it was the wrong
argument for dropping the **validation**, and it left one class with two standards for
"required" - `id`, `state` and `version` refused at construction while the two timestamps were
silently optional. Java gets both: a required field, and a factory that supplies it from the
clock. Pass `self.clock.instant()`, which is what `clock.instant()` is doing on the other side.

`Entity` has no timestamps in either port. A row that needs to know when it last changed almost
certainly has changes worth telling the rest of the system about, which makes it a `Model`.

The write path matches too, as of `EKB-0194`. **Nothing writes `updated_date` but you.** Java
writes `updatedDate` nowhere in `ekbatan-core` - `ModelRepository` only checks the column exists,
and `nextVersion()` carries whatever the row held forward - and this port briefly refreshed it
from the clock on every staged update. It no longer does:

```python
self.copy().updated_date(self.clock.instant()).balance(new).build()
```

on the changes where it means something. The version still moves inside the write, under
`WHERE version = ?`, because that is the optimistic lock rather than a convenience.

The trade is stated plainly: a column named `updated_date` does not move on its own, and a row
that has never been marked still reads as last-updated-when-created. That is the cost of a
framework that does not rewrite a column the application set - which is the property that makes
the column usable for recording when *your* change happened.


## Time is stored as given; the default clock gives naive UTC

> **chosen** - the user's rule (2026-10-19): the persistence layer writes and reads exactly what it is given, no automatic shifting. Java converts at the jOOQ boundary instead. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md), "time as is (EKB-0384-)".

Java's domain holds `Instant`s, and its `InstantConverter` - wired per project through jOOQ's
forced types - turns each into its UTC `LocalDateTime` on the way into a zone-less column and
back into an `Instant` on the way out.

This port converts nothing (`EKB-0384`). A `datetime` goes to the driver as the model, row or
clock held it and comes back as the driver returns it: no zone added, removed or moved, in
models, repositories, `timestamp_column()`, the outbox, notifications, event JSON or CDC rows.
`SystemClock().instant()` gives the current UTC time with no zone attached - the same value Java's
converter writes - so the two ports still agree on what is in the column. What differs is what
Python code holds: a naive `datetime`, not an aware one. It used to be aware UTC (`EKB-0294`);
that conversion is gone.

The cost is the one the user chose to carry: an application that hands in an aware value gets
what its driver does with it (psycopg moves it to the session's zone, asyncpg refuses it,
PyMySQL, aiomysql and SQLite drop the offset), and a clock that gives aware values must be
matched by zoned columns, because the framework compares the two.

## Time kinds are labels on one `datetime`; a JSON time field must declare one

> **forced** - Python has one `datetime` class for Java's `Instant`, `LocalDateTime` and `OffsetDateTime`. The user's design (2026-10-22, `EKB-0419`-). [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md), "time kinds (EKB-0419-)".

Java's types say which kind of time a field holds, and Jackson writes each its own way. Here
`ekbatan.time` gives the three Java names as `typing.NewType` labels over `datetime`: a type
checker keeps them apart, and at run time a value is still a plain `datetime`. `Instant` is held
as UTC digits with no zone attached - what the default clock gives and the zone-less column
holds - where Java's is a point on the timeline; the JSON is the same (`"...Z"`).

Two consequences Java does not have. `Clock.instant()` is the clock's only method - Java's own
`Clock` also has a zone, `millis()` and the rest; this port's `now()` was renamed, no alias. And
**a JSON time field declared plain `datetime` is refused** when the action runs: in Java an
event field cannot be "some kind of time", here it can, and writing it would make the reader
guess. A schemagen record types each time column by kind too - `Instant` for zone-less columns,
`OffsetDateTime` for `TIMESTAMPTZ`, `LocalDateTime` where a forced type says - where jOOQ's
generated record holds whatever the project's forced types make it.

## The per-invocation plan lives in a `ContextVar`

> **forced** - CPython has no `ScopedValue`. A `ContextVar` is the direct analogue.

Java uses a `ScopedValue`. A `ContextVar` is the direct analogue and is per-thread and
per-async-task at once, so the same mechanism works for a threaded service and an asyncio one
without the framework choosing for you.

Both share the property that matters: work handed to a pool from inside a unit of work does not
inherit the plan. That is correct - another thread is another connection, and quietly sharing
one would corrupt both - and it is the one thing about this that surprises people, so
`current_plan()` says so in its error message rather than returning `None`.

## Transactions are the caller's, and batching with them

> **parity** - Neither port's repository opens a transaction. Recorded because the mechanism differs, not the behaviour.

Neither port's repository opens a transaction. Inside one it joins; outside one each statement
commits itself - Java's `db()` is a jOOQ context over a DataSource with auto-commit left on, and
here it is a connection in `AUTOCOMMIT`. SQLAlchemy will not do that by default: an `execute`
outside a transaction quietly begins one, and closing without `commit` rolls it back, so a write
would vanish. `AUTOCOMMIT` is how you ask for what the JDBC driver gives Java for free.

One repository call is therefore **one statement**, however many rows, in both ports. Nothing is
chunked. Splitting a batch that would exceed the server's bind-parameter ceiling - 65535 on
Postgres, a packet size on MySQL - belongs to the caller, because only the caller knows what the
pieces mean if one of them fails.

This port did chunk, at five hundred rows, and opened a transaction to keep the pieces together.
That made large batches work where Java's fail, at the cost of the framework deciding what was
atomic on the caller's behalf. It now does neither.

The consequence is worth stating plainly, and is the same on both ports: a batch update whose
optimistic lock misses on one row raises, and the rows that did match are already written. Java's
`should_throw_optimistic_lock_exception_when_updateAll_with_stale_versions` asserts exactly that.
Inside a unit of work the executor's transaction takes the whole thing back; outside one there is
nothing to take it back. Run a batch inside a unit of work when the pieces belong together.

## Retries match an exception exactly as Java's do

> **parity** - the exact class, and the `__cause__` chain only. Brought to parity in `EKB-0198`, after the reason for diverging was checked and did not hold.

Both ports match the thrown exception's **exact class** and walk **only the explicit cause**:

| | Java | here |
|---|---|---|
| which class matches | `retryConfigs.get(current.getClass())` - exact | `type(error) in exact` |
| what the walk follows | `getCause()` | `__cause__` |

This port did it differently until `EKB-0198`: `isinstance`, so subclasses matched, and the walk
also followed `__context__` - the error that happened to be being handled when this one was
raised, which Python records for free and Java has no equivalent of. Both widened retries, so
the same configuration retried strictly more here than there.

**The argument for diverging was checked and did not survive it.** It was this:

```python
RetryConfig(on=OperationalError)  # "matches every driver subclass of it, as `except` would"
```

There are no driver subclasses. Measured: `sqlalchemy.exc.OperationalError.__subclasses__()` is
empty - SQLAlchemy raises that exact class for every dialect and carries the driver's own error
inside it as `orig`. The headline case for subclass matching was not a real case.

What remains true is the cost, and it is Java's cost too: a policy `on` a class that is only
ever raised as one of its subclasses - `EkbatanError`, `DatabaseError` - matches nothing, and
says nothing about it. Name the class that is thrown.

Two things made the divergence safe to close. Every documented example names `StaleRecordError`,
which has no subclasses; and the switch was applied and the suite run before it was kept - 601
of 603 tests passed untouched, the two failures being the tests that existed to assert the
divergence. Nothing in the framework depended on the wider rule.

## The connection accessors are Java's, exactly

> **parity** - Now identical, including the details. The earlier divergence here is the one this triage exists because of.

`db()`, `dbs()`, `readonly_db()`, `readonly_dbs()`, `tx_db()` and `tx_db_else_db()` mean what
their Java counterparts mean, down to the details that are easy to get wrong:

- **`readonly_db()` is the replica**, falling back to the primary when a shard has none. It does
  not join an open transaction - a replica has not seen what that transaction wrote, so joining
  would be a promise the connection cannot keep.
- **`db()` is the primary** and does not join an open transaction either. `tx_db_else_db()` is
  the one that joins.
- **`dbs()` and `readonly_dbs()` visit only the default shard** when the repository is
  unsharded, so a repository that opted out of sharding cannot scatter onto a sibling's
  databases.
- **The built-in reads use `db()`**, as Java's do: the primary, outside any open transaction.
  Inside a transaction they do not see its uncommitted writes, and each takes a second
  connection from the pool. A read that must see them is a query of your own on
  `tx_db_else_db()`. Choosing to accept lag belongs to the caller, who is the only one who
  knows whether they just wrote the row they are about to read.

This was not always true. An earlier version of this port made `readonly_db()` mean "a read" and
default it to the primary, with `replica=True` to opt in - same routing, different name. It was
recorded here as a neutral difference, which was the mistake: it is a judgement call about whose
job consistency is, and it belonged in `docs/DECISIONS-TO-REVIEW.md` where it would have been
argued about rather than filed.

The built-in reads, too, once joined an open transaction and saw its uncommitted rows, while this
section already said they matched Java. Run side by side on PostgreSQL, Java's `findById` and
`existsById` inside `inTransaction` did not find a row added there; this port's did. They now go
through `db()`.

## A custom query ends with the mapping, as jOOQ's does

> **parity** - both ports end a custom query with the mapping, in the same chain: jOOQ's `fetch(this::fromRecord)`, here `rows(statement).fetch(self.from_record)`. jOOQ has the verb built in; SQLAlchemy has none, so `Db` adds one. Leaving it out would be the divergence - the query in one place and the mapping in a loop afterwards. Relabelled in `EKB-0227`; it was filed as chosen.

The accessors above mean exactly what Java's mean. What they *hand back* differs: a `Db`, which
is a `sqlalchemy.Connection` with one method added.

Java ends a custom query with the mapping, in the same chain as the query:

```java
ctx.selectFrom(WALLETS)
   .where(WALLETS.OWNER_ID.eq(ownerId))
   .and(WALLETS.STATE.ne(WalletState.DELETED.name()))
   .fetch(this::fromRecord);
```

SQLAlchemy has no verb for it. A bare connection leaves the mapping to the caller, afterwards:

```python
rows = conn.execute(statement).all()
return [self.from_record(row._mapping) for row in rows]
```

So `Db` supplies the missing one, and the order matches the Java:

```python
return conn.rows(statement).fetch(self.from_record)
```

Each terminal hands the mapper the row read by column name, the record `from_record` takes - as
jOOQ hands `fromRecord` the `WalletsRecord` it fetched.

`conn.rows(statement)` returns a `FetchResult`, whose terminals each take the mapper:
`fetch`, `fetch_first`, `fetch_one`, `fetch_one_or_none`, `each`, plus `yield_per`,
`partitions` and `unique` for shaping. Full table in
[`docs/database/repositories.md`](docs/database/repositories.md#examples).

Three things about it are deliberate:

- **It adds, it does not replace.** `Db` is a subclass, so `execute`, `begin`,
  `exec_driver_sql` and the rest are SQLAlchemy's, unchanged. A query with no rows to map - a
  count, a rowcount, an `EXISTS` - still goes through `execute`, because `rows()` would be
  claiming a shape the answer does not have.
- **The names are not jOOQ's.** There, `fetchOne` means *at most one, raise if there are two*
  and `fetchAny` means *the first*. Copying the spellings would give a `fetch_one` that does not
  mean "fetch one", so these say what they do instead.
- **`FetchResult` holds a `Result`; it does not subclass one.** Subclassing was shorter and was
  rejected: reproducing a `Result` means setting `_real_result`, `_metadata`,
  `_post_creational_filter` and `_unique_filter_state` by hand - four private attributes whose
  contract can change in a patch release. Everything `FetchResult` calls is documented API.

What it does **not** do is filter soft-deleted rows. A statement you build yourself carries
what you put in it, in both ports - which is why the example repositories write
`WALLETS.c.state != WalletState.DELETED.name` out, exactly where the Java writes
`.and(WALLETS.STATE.ne(...))`.

## An unregistered shard routes to the default, as Java's does

> **parity** - the same fallback, line for line. **This section claimed the opposite until `EKB-0199`, and was wrong.** It described a code path routing never takes.

Both ports fall back to the default shard for one they do not hold:

| | |
|---|---|
| Java | `transactionManagers.containsKey(shard) ? shard : defaultShard` |
| here | `if shard is None or shard not in self._managers: return self.default_shard` |

The fallback has a real use - a staging database with one shard can read production's ids
instead of refusing them - and a real cost, which is now equally the cost in both ports: a
mistyped shard identifier is silent, and the writes land on the default where nobody thinks to
look for them. The bug it produces is invisible until somebody counts rows.

Running unsharded on purpose is `NoShardingStrategy`: every key resolves to the default and its
scatter accessors visit only that shard.

**Both ports gained a warning and a setting in `EKB-0200`**, written here and written for Java in
the same session. The fallback was never the problem; its silence was. It now logs every time it
fires, and `unknown_shard_policy="fail"` / `unknownShardPolicy: fail` turns it into an error for
a deployment that holds the whole layout - on reads as well as writes, because the policy is
applied where routing resolves. Omitting it selects `USE_DEFAULT_SHARD` in both, so nothing
changes for anyone who does not ask. The value is read the same way in both (`EKB-0303`): any case,
any separators, empty means the default.

### What this section used to say, and why it was wrong

It said the registry **refuses** an unregistered shard, quoting a real error message:

```
ValidationError: no database registered for shard 9:9; registered: ['0:0']
```

That message exists, and `DatabaseRegistry.manager()` really does raise it - where Java's
`primary.get(shard)` returns `null` and fails later with a `NullPointerException`. The error is
better. But it is not the routing behaviour, because every routing path resolves through
`effective_shard` first (`repository.py:371-376`), which has already replaced an unregistered
shard with the default. Nothing reaches `manager()` with one.

So a true sentence about a real method was filed as the answer to a question it does not answer.
Measured, on a two-shard registry:

```
effective_shard(9:9) -> 0:0                         <- routing: silently the default
manager(9:9)         -> ValidationError: no database registered for shard 9:9
```

Found by being asked for a config example: the sharded example's own comment - *"where a row
goes when its id names a shard this deployment has not configured"* - contradicted this section,
and the comment was right. Same failure as `readonly_db` and the `OperationalError` subclasses:
written once, argued convincingly, never run.

## Deleting

> **parity** - The same as Java in both ports, after some wandering.

The same as Java, after some wandering: there is no delete of any kind on the repository in
either port. `GenericState.DELETED` is the convention, every read filters it, and what deletion
*means* belongs to the model:

```python
def delete(self) -> Wallet:
    if self.state is WalletState.DELETED:
        return self
    return self.copy().with_event(WalletDeleted()).state(WalletState.DELETED).build()
```

then `repo.update(wallet.delete())`, or `plan.update(wallet.delete())` inside a unit of work.

This port briefly had `Repository.delete()` and `Repository.hard_delete()`. Both are gone. The
first wrote the DELETED state without emitting anything, so a wallet vanished from every read
in the system and nothing downstream was told; the second issued a physical `DELETE`, bypassing
the optimistic lock and leaving published events describing a row that no longer existed. An
application that genuinely needs a physical delete writes it against its own rule - see
`WalletRepository.erase` in the example, which refuses a wallet that still holds money.

## The scheduler is this framework's, not a dependency's

> **forced** - There is no maintained, synchronous, multi-dialect, database-backed scheduler for Python. Checked against the ecosystem rather than assumed.

Java delegates distributed jobs to db-scheduler. Python has no equivalent, which was checked
against the ecosystem rather than assumed. What this needs is all four of: maintained,
synchronous, multi-dialect, and database-backed. Every candidate fails at least one.

| candidate | stars | why not |
| --- | --- | --- |
| APScheduler 3.11 | 7.6k | its own FAQ: *"How do I share a single job store among one or more worker processes? Short answer: You can't."* Two instances give duplicate execution - the opposite of what a distributed job is |
| APScheduler 4.0 | same | has a shared data store, but its tracking issue has been open since September 2020 and `4.0.0a6` still says "should not be used in production" |
| procrastinate | 1.4k | Postgres only |
| pgqueuer | 1.5k | Postgres only |
| chancy, BeanQueue, dmi-scheduler | 286 / 28 / - | Postgres only |
| pyncette | **12** | the only real match on paper - leases, and Postgres *and* MySQL. But asyncio-only, so every `DistributedJob.run` would become `async def` in application code; and one contributor, 112 downloads a month |
| huey + SqlHuey | 5.5k | the one that is genuinely multi-dialect without a broker - SqlHuey stores the queue through peewee, so MySQL and Postgres both work. But each consumer's scheduler enqueues periodic tasks on its own, and `lock_task` is a mutex rather than a claim, so two consumers run every slot twice |
| celery, dramatiq, arq, rq, taskiq | large | need a broker - Redis, RabbitMQ, NATS - so not database-backed |
| hatchet | - | an SDK for a server you deploy, not a library |
| dbos | - | Postgres only |
| prefect, dagster, airflow, temporal | large | orchestration platforms; you would deploy Airflow to run a fan-out loop |
| sqlalchemy-celery-beat | 82 | stores the *schedule* in Postgres/MySQL/SQLite, which is the right half - but it is Celery Beat, and Celery's docs say *"you have to ensure only a single scheduler is running for a schedule at a time, otherwise you'd end up with duplicate tasks"*. The only lock in the package guards a `last_run_at` write-back, not a slot |
| django-celery-beat | 1.8k | the same, plus Django |
| `db-scheduler` on PyPI | - | unrelated package: 0.1.4, no cluster, claim or heartbeat in it |
| aistudio-scheduler-lock | - | the one ShedLock-alike; requires Django < 4.0 |

The top two rows were measured rather than taken from their documentation. Two processes, one
shared store, one job scheduled every second for eight seconds:

| | runs | outcome |
| --- | --- | --- |
| huey + SqlHuey | 16 over 8 slots | every slot run by both instances |
| huey + SqlHuey + `@huey.lock_task` | 16 over 8 slots | identical - the lock changes nothing |
| sqlalchemy-celery-beat | 14 over 7 slots | every slot dispatched by both instances |
| APScheduler 3.11 + SQLAlchemy job store | 14 over 8 slots | six slots run twice |
| this `JobRegistry` | 8 over 8 slots | four each, no slot twice |

huey's `lock_task` is worth its own line, because it looks like the answer and is not. It works
exactly as documented - a second acquire while the first is held is refused - but it is released
when the task returns, so two runs of one slot that do not overlap both proceed:

    held concurrently        second acquire refused
    acquire, release, again  second acquire succeeds

A `ttl` on that lock does not rescue it either, and this is worth writing down because the
suggestion looks so reasonable: `lock_task(name, ttl=...)` raises `NotImplementedError: ttl is
not supported by this storage` on every backend except Redis - including the SQL one, which is
the whole reason huey was a candidate. And even on Redis the ttl is a safety net for a holder
that crashed, not a claim on the slot: `TaskLock.__exit__` deletes the key unconditionally when
the task returns.

That is the distinction the whole table turns on. A mutex answers "is someone running this right
now"; a claim answers "has this slot already been taken". Only the second prevents duplicate
execution, and duplicate runs of a fast task are sequential, so the mutex is never contended.

For Celery Beat the probe intercepted `apply_entry` - the moment beat decides a slot is due -
so no broker was involved and nothing else could account for the number.

One pattern runs through the whole table, and it is the useful thing to take from it: several
Python libraries persist the *schedule* in a database. What none of them does is let two
processes race for a slot and have exactly one win. Those are different problems, and only the
second is why db-scheduler exists. A library that stores the schedule in Postgres and then tells
you to run exactly one instance has solved the half that was never hard.

pyncette is the interesting one and the reason this table exists rather than a sentence. It is
genuinely the right shape - a distributed scheduler with pluggable Postgres and MySQL backends -
and it would still be the wrong dependency. What would be delegated is the atomic claim, the code
that stops five servers each running the nightly report; a defect there is not a crash, it is the
report going out twice. That is not a thing to take from a solo project with a hundred downloads a
month, whatever its quality.

So the claim-and-heartbeat loop is implemented here - about two hundred lines - against
db-scheduler's own table layout, so a Java service and a Python service coordinate through one
`scheduled_tasks` table rather than each running the nightly report. That last requirement rules
out every row above on its own: none of them would use db-scheduler's schema.

Schedules are `Every`, `FixedDelay` and `Daily`. Cron is not built in: parsing it needs a
dependency, and a `Schedule` is one method.

## A handler extends one of two bases

> **chosen** - by the user (2026-10-25): handlers take Java's shape, a base class with `name()`, `aliases()`, `event_type()` and `handle()`. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md), "Event handlers extend a base class with Java's methods (EKB-0548-)".

Java's `EventHandler` is an interface a handler implements. Here it is an abstract base class a
handler extends, with the same four methods in the same order, `aliases()` empty unless
overridden. The one difference is a second base, `AsyncEventHandler`, whose `handle()` is
`async def` - for a handler that awaits `ekbatan.aio` - which Java, having no async side, does
not need. The registry refuses a handler that extends neither, as Java's compiler refuses a class
that does not implement the interface, and one whose `handle()` is of the other kind.

## A data source wraps SQLAlchemy's engine

> **chosen** - by the user (2026-10-24): `engine_for` became `DataSource.from_config`, and the parts that take a pool take only a data source, as Java's take only a `ConnectionProvider`. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md), "DataSource and AsyncDataSource (EKB-0524-)".

Java's `ConnectionProvider` holds a HikariCP pool and offers `acquire`, `release`, `evict`,
`getDataSource`, `jdbcUrl` and `close`. Here `DataSource` - `AsyncDataSource` in `ekbatan.aio` -
holds SQLAlchemy's engine and offers `sqlalchemy_engine`, `url`, `dialect` and `close()`
(`aclose()`): taking a connection, giving it back and throwing a broken one away are SQLAlchemy's
`connect()`, `close()` and `invalidate()` on that engine. What is the same:

- **one way to build one**: `from_config(config)`, as `hikariConnectionProvider(cfg)` is Java's
  only public way to a provider (its constructor is private; calling `DataSource(...)` here is a
  `TypeError` naming `from_config`);
- **the same takers**: the transaction manager, the SQL lock providers' `data_source(...)`
  (Java's `connectionProvider(...)`) and the job registry's - each refuses anything else, an
  SQLAlchemy `Engine` included;
- **the same ownership**: a transaction manager's `close()` closes the data sources it was given;
  lock providers and the job registry close none.

What differs: a data source closed here can be used again - SQLAlchemy opens a new pool - where a
closed Hikari pool refuses; and a data source used or closed in a process `fork()` copied it into
raises `ForkedProcessError`, as every object here that owns connections does.

## Names that still differ, and why

> **parity** - A table of names, each differing because the thing differs. No behaviour.

Everything that names the same concept now carries the same name, so a reader moving between the
two ports recognises what they are looking at. What is left differs because the thing itself
differs, not because the name was chosen freely:

| Java | here | why |
| --- | --- | --- |
| `ModelId`, `Identifiable`, `ShardAwareId` | - | interfaces that exist to name a shape Python reads off the object |
| `ShardedUUID`, `Backoff` | `sharded_uuid()`, `backoff()` | a class holding only static methods is a function here |
| `Transaction` (package-private) | - | the auto-commit dance SQLAlchemy already does; the public `Transaction` here is the counterpart to the `DSLContext` Java hands the block, not to this |
| `ConnectionProvider` | `DataSource`, `AsyncDataSource` | named after the `DataSourceConfig` it is built from; see "A data source wraps SQLAlchemy's engine" below |
| `Entity.Builder`, `Model.Builder` | `EntityBuilder`, `ModelBuilder` | same two builders, unnested - a nested class is not a Python idiom, and `ModelBuilder` can then inherit rather than repeat |
| `resolveShardIdentifier()` | `.shard` | a property, because it reads a field of the id rather than doing work |
| `ModelId.stringValue()` | `str(id)` | what `__str__` is for; `Id.toString()` in Java returns the same string |
| `DatabaseRegistry.transactionManager(id)` | `.manager(shard)` | Java repeats the return type in the name; Python does not |
| `DatabaseRegistry.defaultTransactionManager()` | `.default` | a property, as it reads no argument |
| `DatabaseRegistry.allTransactionManagers()` | `.shards` + `.manager()` | its one caller is the fanout job, which wants the shard as well as the manager |
| `ShardingStrategy.resolveShardIdentifierById(id)` | `.shard_for_id(key)` | same idiom - the return type is `ShardIdentifier` and `shard_for` says so |
| `ShardingStrategy.resolveShardIdentifier(persistable)` | `.shard_for(persistable)` | as above |
| `ShardedUUID` (wrapper class) | `sharded_uuid()`, `read_shard()`, `uuid_instant()` | the class holds one `UUID` and three derivations of it; here they are three functions on a `UUID` |
| `LockKeyHash.hashUtf8(key)` | `lock_key_hash(key)` | a class of static methods is a function here - the value it computes is identical, and pinned by test |
| `ExecutionConfiguration.retryConfigs` (a `Map<Class, RetryConfig>`) | `.retry_configs` (an ordered tuple) | the class Java keys the map by lives on each config here as `on`, which can name several at once - `except (A, B)` is one clause, and a map key cannot be |
| `Retry` (class) | folded into the executor's loop | it has one caller and no state beyond what that caller already holds |

One place where matching Java costs something: `ExecutionConfiguration` sits next to
`ShardingConfig`, `ShardGroupConfig`, `ShardMemberConfig` and `DataSourceConfig`, because that is the inconsistency Java
has and the name is the thing worth matching. `RetryConfig` follows Java rather than the
`RetryPolicy` it was called here.

## A row checks its own invariants, because Python has no constructor to hide them in

> **forced** - A frozen dataclass has no constructor body; `__post_init__` is where the same checks go.

Java's `Model` and `Entity` constructors validate: `id`, `state` and `version` non-null,
`version >= 1`, `events` non-null and copied. `ModelTest` and `EntityTest` pin each one by
message. Those checks are here too, in `__post_init__`, in Java's order (`id`, `events`, `state`,
`created_date`, `version`, the version's range, then the model's own fields), for the same
reason - without them the mistake surfaces as an `IntegrityError` from inside a batched insert,
naming a column and a driver, several frames from the line that made it.

`version` has no default, as Java's has none (`EKB-0292`): forget `with_initial_version()` and
`build()` refuses with "version cannot be null", and `increase_version()` with none set refuses
the same way. A setter never called is passed to the constructor as `None`, as a Java builder
field is null until set, so it is refused by name - "id cannot be null" - rather than with the
constructor's `TypeError`. Calling the dataclass constructor directly without a field still
raises `TypeError`: that is the call Java does not let you write.

One thing follows from Python that does not follow from Java: the events tuple is genuinely
copied: `events` is annotated as a tuple, nothing
stops a list being passed, and a frozen model holding the caller's list is not frozen - append
to it afterwards and the model gains an event it was never built with, which the persister then
writes, in a transaction that had already decided what it was writing. Java says
`List.copyOf(...)` for exactly this and gets immutability out of it as well.

### The application's own fields are checked too, which Java's base class cannot do

Java validates a subclass's fields one `Validate.notNull` at a time, in every model's
constructor:

```java
Wallet(WalletBuilder builder) {
    super(builder);
    this.ownerId  = Validate.notNull(builder.ownerId,  "ownerId cannot be null");
    this.currency = Validate.notNull(builder.currency, "currency cannot be null");
    this.balance  = Validate.notNull(builder.balance,  "balance cannot be null");
}
```

It has no choice: a Java base class cannot enumerate a subclass's fields. A dataclass can, so
the rule is stated once, in `Persistable.__post_init__`, and reads the annotation:

| Java | here | `None` |
|---|---|---|
| `String currency` | `currency: str` | refused |
| `Optional<String> note`, present | `note: str \| None` | — |
| `Optional<String> note`, absent | `note: str \| None` | accepted |
| a `null` `Optional` **reference** | *no counterpart* | — |

Same semantics, one sentinel instead of two: Python's absence **is** `None` rather than a
wrapper that can itself be missing, so Java's fourth row has nothing to map to. The gain is that
it covers the fields an author would have forgotten as well as the ones they remembered -
`ekbatan-py-examples`' `Wallet` accepted `owner_id=None`, `currency=None` and `balance=None`
until `EKB-0193`, because three lines Java writes by hand had not been written.

Cached per class, since a dataclass's fields are fixed at definition: **1.6 us** per
construction, measured over 200,000.

## A model's own rules go in `validate()`, which every model and entity must define

> **chosen** - by the user (`EKB-0497`): Java's subclass constructor body becomes a required `validate()`, run after the built-in checks on every row made. See [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md#mandatory-validate-ekb-0497-).

In Java a model's constructor is its one central place for validation: `super(builder)` runs the
base checks, then the subclass's own lines - `Validate.notNull(...)` per field, and any rule
such as a balance that may not be negative. Every way of making a `Wallet` goes through that
constructor, so none can skip it.

A frozen dataclass has no constructor body to write. Here the base's `__post_init__` runs the
built-in checks - the `None` checks read off the types, above, so the `Validate.notNull` lines
need no counterpart - and then the model's `validate()`, in Java's order. Every path that makes
a model goes through it: the constructor, `build()`, `copy()...build()`, `evolve`, and
`from_record`.

`validate()` is **required**, where Java's constructor rules are optional lines. `validate` is
abstract, so a type checker flags a model without it, and the class is refused when it is
defined; `pass` says a model has no rules beyond its types.

A model may still write its own `__post_init__`. It is wrapped when the class is defined, so the
built-in checks and `validate()` run around it whether or not it calls `super().__post_init__()`
- Java's constructor chaining, which cannot be forgotten, made true here as well. Until
`EKB-0497` forgetting `super()` switched every built-in check off.

## Equality is the same row at the same version, as Java's is

> **parity** - `Model` compares class, id, state, version and both timestamps; `Entity` compares class, id, state and version; both hash by id. Java's `equals` and `hashCode`, field for field. Brought to parity in `EKB-0228`; until then a frozen dataclass compared, and hashed, every field.

Two wallets with the same id and version but different balances are equal, in both ports. That
is also how Python compares a saved record - Django by primary key and class, SQLAlchemy by one
object per row, *Architecture Patterns with Python* by the entity's identifier; comparing every
field is what those use for value objects, which have no identity of their own.

Comparing every field had a second cost the old entry did not mention: it **hashed** every field
too, so a model with a list or dict field - a JSON column - raised `unhashable type` the moment it
went into a set or became a dict key. Hashing by id, as Java does, cannot.

Mechanism, not behaviour:

- **Each model class is handed the rule as it is defined.** `@dataclass(frozen=True)`, which every
  model is declared with, writes a whole-row `__eq__` and `__hash__` unless the class defines them
  itself, and a base class's do not count. So the framework copies the inherited pair onto each
  class before its decorator runs - no model has to say anything.
- **A model that writes its own `__eq__` keeps it**, and its subclasses inherit that choice.
- **A different class is `NotImplemented`, not `False`**, which is how Python spells Java's
  `getClass() != o.getClass()`; `==` still answers `False`.

## The layout is Java's: groups, members, and a named map of datasources on each member

> **parity** - `ShardingConfig` / `ShardGroupConfig` / `ShardMemberConfig`, level for level, with the `configs` map keyed exactly as Java's is. Brought to parity in `EKB-0222`; until then a shard had a fixed `primary` / `replica` pair and nothing else.

```java
shardMemberConfig().member(0).name("global-eu-1")
    .primaryConfig(primary).secondaryConfig(replica).withConfig("lockConfig", lock).build()
```

```python
ShardMemberConfig(member=0, name="global-eu-1", configs={
    "primaryConfig": primary, "secondaryConfig": replica, "lockConfig": lock,
})
```

Java's rules, each with Java's message: `"primaryConfig"` is required, `"secondaryConfig"` is
optional, any other name is the application's and is read back with `config_for(name)`; a group
needs a name and at least one member; a layout needs a default shard and at least one group. The
map is copied and read-only, as `Map.copyOf` is. The keys are Java's spelling, so one layout
describes both ports.

The registry builds pools for the primary and secondary only, as `fromConfig` does. Anything else
on the member is built by the application with `DataSource.from_config(config)` - Java's
`ConnectionProvider.hikariConnectionProvider(config)` - and the examples now declare their
scheduler and lock pools as `"jobsConfig"` and `"lockConfig"` exactly as Java's `application.yml`
does, rather than calling `create_engine` beside the layout.

Differences in mechanism, not behaviour:

- **Constructors take keyword arguments, where Java has builders**, as every config class in this
  port does. The builder's `primaryConfig(...)` / `withConfig(name, ...)` are entries in the
  `configs` literal.
- **`ShardingConfig.members()` and `member(shard)`** walk the layout. Java inlines that walk in
  `fromConfig` and carries `findDefaultMember` in each DI module; four places need it here.
- **Nothing binds the layout from a property file.** Java's `ShardingConfigYamlTest` and
  `ShardingConfigJacksonBindingTest` bind it with Jackson; their assertions about the bound layout
  are ported against a layout built directly. `PropertyKeyNormalizer` has no counterpart.
- **Two checks Java does not make**: a `(group, member)` configured twice is refused - in Java the
  second pool silently replaces the first in the registry and the first is left open - and every
  member's primary must share a dialect.

## SQLite is a dialect here and is not one in Java

> **chosen** - An addition. Java refuses everything but the three. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

`DataSourceConfig` accepts `sqlite://`. Java accepts PostgreSQL, MySQL and MariaDB and refuses
everything else, including H2.

It is not a supported production dialect here either - what it buys is that the behavioural
suite runs a second after you write a test, with no daemon, and then runs against the real three
before anything is believed (`--dialects=all`). A framework whose tests need Docker to run at
all is a framework whose tests stop being run.

## A write without RETURNING rebuilds what it wrote

> **forced** - MySQL has no `RETURNING`. Both ports branch; the branches differ because the drivers do.

MySQL has no `INSERT ... RETURNING` and no `UPDATE ... RETURNING`, so there is nothing to read
back. Java takes the obvious branch - `if (dialect.equals(SQLDialect.MYSQL)) { execute(); return
List.copyOf(domainObjects); }` for inserts, `return domainObject.nextVersion()` for updates - and
hands the caller their own objects.

This port cannot. Java's types make a raw `UUID` unpassable where an `Id<Wallet>` belongs, and
`nextVersion()` goes through a builder that drops the event list; here neither is true, so the
same branch returned a model still carrying the events it had just persisted - which the next
update would write a second time - and a raw uuid where every other database returned a wrapped
one. Both paths now rebuild through the same readers a query uses, so one call has one shape on
four databases.

What is still dialect-dependent, in both ports, is *scale*: a `Decimal("2.50")` written to a
`NUMERIC(19,4)` column comes back as `2.5000` where RETURNING exists and as `2.50` where it does
not. Java's `should_preserve_converted_values_when_updateAll_batches_multiple_rows` sidesteps this
by re-reading rather than asserting on the return value, and the test here does the same.

## `plan.add` hands back the row you passed, as Java's does

> **parity** - Java's `ActionPlanTest.add_returns_the_entity` asserts `isSameAs`, and so does this port's test. Brought to parity in `EKB-0192`.

This used to hand back a stamped copy, because the framework filled in `created_date` and
`updated_date` and a caller holding the unstamped original was holding something that did not
describe the row about to be written. That reasoning was sound and its premise is gone:
`created_date` is required at construction now, so there is nothing left to fill in and `add`
hands back the object it was given.

`plan.update` matched Java already - `entity.nextVersion()` on both sides, so the version has
moved and the rest of the unit of work reads forward.

`add_all(None)` is the one difference left here. Java tolerates a null collection and returns
empty; here it is a `TypeError`. `None` where an iterable belongs is a mistake at the call site,
and returning empty lets it through to become "this unit of work staged nothing" - which commits
a sentinel row and looks like success.

## The test-support API is Java's `ActionSpec` / `ActionAssert`, plus the data underneath

> **parity** - `ActionSpec` and `ActionAssert` are ported method for method, with Java's failure messages word for word. Brought to parity in `EKB-0219`; until then only `run_action` existed.

```java
ActionSpec.of(new WalletDepositMoneyAction(clock, wallet_repository))
    .withPrincipal(alice)
    .execute(new WalletDepositMoneyAction.Params(walletId, amount))
    .assertUpdated(Wallet.class, w -> assertThat(w.balance).isEqualTo("150.00"))
    .assertEmitted(WalletMoneyDepositedEvent.class, 1)
    .assertNoAdditions();
```

```python
(ActionSpec.of(WalletDepositMoneyAction(clock, wallet_repository))
    .with_principal("alice")
    .execute(WalletDepositMoneyAction.Params(wallet_id=wallet_id, amount=amount))
    .assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
    .assert_emitted(WalletMoneyDepositedEvent, 1)
    .assert_no_additions())
```

Every method has Java's name in snake case, Java's overloads as optional arguments, and Java's
semantics: additions and updates match the **exact** class (`plan.changes().get(type)`), events
match subclasses (`isInstance`), and a plain `assert_emitted(type)` means *at least one* while
every other form is exact. Java's 38 `ActionSpecTest` cases are ported case for case.

Two differences in mechanism, both because Python would otherwise let a test pass by accident:

- **A verifier's return value counts.** Java's is a `Consumer` that asserts inside itself and
  returns nothing. A Python `lambda` cannot hold an `assert`, so it returns its verdict -
  `lambda w: w.balance == x` - and a literal port would throw that verdict away and pass
  whatever the balance was. So a verifier that returns a falsy value fails; one that returns
  `None` must have asserted itself, as Java's does. Both spellings work and neither can pass
  silently.
- **`assert_throws` is `try` / `except` / `else`.** Java raises "nothing was thrown" inside the
  `try`, which is safe there because `AssertionError` is an `Error`, not an `Exception`. In
  Python it *is* an `Exception`, so Java's shape would catch its own report when the expected
  type is `Exception` and pass. A test pins this.

`run_action` and its `StagedWork` stay. They are the data `ActionAssert` reads, and a test that
reads better as plain `assert` can use them directly.

`VirtualClock` is the opposite case: it is ported in full, three modes and all, because those
are behaviour rather than assertion sugar. One addition - `VirtualClock(instant)` starts frozen
at that instant, where Java's constructor takes a real clock and starts running. `VirtualClock()`
still means what Java's does.

## Dispatch matches Java, in parallel and in statement count

> **parity** - handlers in parallel (`EKB-0204`) and outcomes in one `UPDATE` per bucket (`EKB-0205`). Both halves closed; only the thread kind still differs.

Java invokes a batch's handlers in parallel on virtual threads and then commits the outcomes in
one batch `UPDATE` per bucket - one for the successes, one for the pre-flight expiries, one for
the post-failure expiries, and one per distinct `attempts` value among the retries. Two halves,
and only one of them ported.

**The handlers now run in parallel** (`EKB-0204`), one thread per notification, bounded by the
batch size exactly as Java's is - Java caps nothing of its own either, so there is no setting
here. The pool is per shard, which is where Java's executor lives too, and it is built once and
reused: a round happens every poll delay forever, and a `ThreadPoolExecutor` grows lazily, so
declaring one the width of a batch costs nothing until a batch is actually that wide. Measured,
a handler modelled as 20 ms of waiting:

| batch | in series | pooled | speed-up | threads |
|---|---|---|---|---|
| 1 | 24 ms | 25 ms | 0.9x | 0 |
| 10 | 234 ms | 28 ms | 8.3x | 10 |
| 50 | 1142 ms | 27 ms | 41.9x | 50 |
| 100 | 2376 ms | 28 ms | 83.9x | 100 |

A single notification is delivered inline, on no thread at all. That threads earn their keep
here at all is because a handler is application code waiting on something almost every time.

**This changed a promise.** Handler code used to be guaranteed to run alone and now does not.
Java's never had that guarantee, so Java's users never had it to lose; ours did.

**The outcomes now go one `UPDATE` per bucket** (`EKB-0205`). Rows that decided the same
thing - same state, same resolved attempt count, same retry delay - are one
`UPDATE ... WHERE id IN (...)`, and retries group by attempt count exactly as Java's
`failedRetryByAttempts` does. Counted:

| batch | shape | before | after |
|---|---|---|---|
| 100 | all succeed, first attempt | 100 | **1** |
| 100 | mostly ok, retries at two depths, one dead | 100 | **4** |

Four is Java's number, and it falls out of the same four buckets: successes, pre-flight
expiries, post-failure expiries, and one per distinct attempt count among the retries.

Two things had to change for rows to be able to share a statement, and both moved toward Java
rather than away. `_Outcome` carries a retry *delay* rather than a resolved timestamp - resolved
per row it differs by microseconds and no two rows can ever agree. And the batch resolves one
`now` for all of them, which is what Java's `postInvokeNow` already was.

One behaviour does differ. Java treats "no handler is registered under this name" as a delivery
failure, increments `attempts`, and lets the row expire through the retention window. Here the
row is left `PENDING` with its attempt count untouched and its retry rescheduled: a handler
removed by a bad deploy comes back, and its queue should still be there when it does. The row
still expires on the same retention window either way.

## The framework declares the shape of its tables and nothing about their physical design

> **parity** - Java's framework ships no DDL either: `DSL.table(DSL.name(...))` references, and the indexes in the application's migrations. Brought to parity in `EKB-0215`, after being absent from this file entirely.

`ekbatan.schema` gives `events`, `event_notifications` and `scheduled_tasks` as SQLAlchemy
`Table` objects. That is not a divergence: SQLAlchemy needs a `Table` to build a query, the way
jOOQ needs a `Table<?>`, and Java's framework hand-writes exactly the same references -

```java
private static final Table<?> NOTIFS = DSL.table(DSL.name(SCHEMA, TABLE_NAME));
private static final Field<String> HANDLER_NAME =
        DSL.field(DSL.name(SCHEMA, TABLE_NAME, "handler_name"), String.class);
```

Note what those are: *references*, not definitions. There is no `CREATE TABLE` and no
`CREATE INDEX` anywhere in `ekbatan-core`, `ekbatan-events` or `ekbatan-distributed-jobs`. The
tables are created by the application's Flyway migrations - `V0001__eventlog.sql`,
`V0002__scheduled_tasks.sql` - and jOOQ generating the `eventlog` schema in the examples is for
the *application's* code; the framework never touches the generated classes.

**Until `EKB-0215` this port declared three indexes in `ekbatan.schema`, and that was the cause
of a gap somebody had already measured and filed.** One declaration cannot be
`WHERE delivered = FALSE` on PostgreSQL and a plain composite on MySQL, so it was the composite
everywhere - 3,528 kB against Java's 56 kB on 500,000 rows with a 1% backlog. The fix filed at
the time was "dialect-aware index construction in `ekbatan.schema`", which would have built more
machinery on top of the mistake. The mistake was being there at all.

They are gone. Every PostgreSQL migration in this repository now emits the partial form, MySQL
and MariaDB get the predicate as a column, and the suite's own schema is built by migration
files generated from the metadata rather than by `create_all` - which is what Java's integration
tests do with Flyway, and is now the only way those indexes are exercised at all.

**None of this was in this file.** `schemagen` is one of the larger design decisions in the port
and had no entry; that the framework's own tables are *built* rather than reflected, where
Java's jOOQ reflects them from the database like any other, had no entry either. Found by being
asked what Java does about `scheduled_tasks`.

## schemagen is jOOQ's code generator, configured from `pyproject.toml`

> **chosen** - Java's step is jOOQ in the build script; this is the same job for SQLAlchemy, with jOOQ's settings and forced types read from `[tool.ekbatan.schemagen]`. Each call is in [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md), "schemagen with forced types".

The settings are jOOQ's - schemas, includes, excludes, forced types matched by column name and by
SQL type, a converter - and so are the built-in forced types, which are the ones every Java
example's build copies. The user-facing page, [`docs/database/schemagen.md`](docs/database/schemagen.md),
explains them without Java; the mapping, setting by setting, is here:

| Java (`generateJooqClasses`, `<generator>`) | here |
| --- | --- |
| `migrationLocations` | `migrations` |
| `outputDirectory`, `basePackageName` | `out` |
| `jooq { withContainer { image } }` | `image` |
| `schemas`, `<inputSchema>` | `schemas` (the connection's own schema is always read) |
| `outputSchemaToDefault` | always: the connection's own schema is written unqualified |
| `<includes>`, `<excludes>` | `includes`, `excludes` |
| `includeFlywayTable(false)` | always: migration history tables are never generated |
| `database.withForcedTypes(ForcedType()...)` | `[[tool.ekbatan.schemagen.forced-types]]` |
| `.withName("...")` | `name = "..."` |
| `.withUserType(...)` + `.withConverter(...)` | `converter = "..."`: a plain class with `from_db`/`to_db`, wrapped around the column's own type as `converted(<type>, Converter())` |
| a ready-made jOOQ `Binding` | `sqlalchemy-type = "..."`: a library's SQLAlchemy type (named `user-type` for a day, `EKB-0328` renamed it) |
| `.withIncludeExpression(...)` | `include-expression = "..."` |
| `.withIncludeTypes(...)` | `include-types = "..."` |
| `.withExcludeExpression`, `.withExcludeTypes`, `.withNullability` | `exclude-expression`, `exclude-types`, `nullability` |
| the forced types every example copies | built in; `default-forced-types = false` to drop them |
| Gradle reruns the task when a migration changes | Make reruns it; the module checks its fingerprint on import |

Where the output differs:

- **A SQLAlchemy `Table` and a `dict` record, not a table class and a jOOQ record.** jOOQ
  generates `Wallets` (the table) and `WalletsRecord` (its row). Here `WALLETS` is a `Table` and,
  since `EKB-0408`, `WalletsRecord` is generated beside it, under jOOQ's name: since `EKB-0485`
  a subclass of `dict` with a key per column, typed as the column reads (a converter's column
  as its user type, as jOOQ's `userType`). A column is read by its getter,
  `record.column_wallet_id()` (preferred), or as `record["wallet_id"]` - jOOQ's is `getWalletId()`; the prefix here is `column_`
  so that no column name can clash with a `dict` method. No setters, no `store()`, no
  attachment to a connection: it is the `dict` an insert is given, and a type checker's
  contract. One module, `schema.py`; a table in another schema is exported as `SCHEMA_TABLE` /
  `SchemaTableRecord` rather than in a package of its own.
- **The framework's tables are built from `ekbatan.schema`, not reflected.** Java reflects them
  on PostgreSQL and leaves them out on MySQL and MariaDB.
- **MariaDB `JSON` is `json_column()`.** It is `LONGTEXT` with a check there; Java's `(?i:JSON)`
  forced type never matches it, and jOOQ generates a `String`.
- **A MariaDB `CHAR(36)` named like an id is a uuid**, as on MySQL. Java's MariaDB build forces
  nothing, because its uuids are native `UUID`; this port's MariaDB examples store `CHAR(36)`.
- **A PostgreSQL `JSON` column reads as a dict** (`JSON(none_as_null=True)`), where jOOQ gives
  `org.jooq.JSON`. Neither forces it.
- **An enum is `Enum("a", "b")`** with string values; jOOQ generates an enum class.
- **Views go in a second `view_metadata`**, so `create_all` does not create a table by a view's
  name. jOOQ generates them beside the tables.
- **A type SQLAlchemy has no class for is `sql_type("<the database's spelling>")`**, which
  `create_all` builds and which reads what the driver gives (usually `str`), where jOOQ uses
  `Object` with a warning. Before `EKB-0328` it was `NullType()` with a note, which `create_all`
  could not build.
- **A converter is wrapped around the column's own type**, `converted(NUMERIC(...), Money())`,
  and receives what the column would give without it - on a timestamp, the `datetime` the driver
  read, as it is. jOOQ's converter receives the JDBC type of the
  column; the effect is the same, the layering is explicit here.
- **The module checks a fingerprint of the migrations on import**, because nothing in Python
  rebuilds it before the application runs, as Gradle does.

## Migrations run on yoyo, with the lock, checks and history yoyo does not have

> **forced** - There is no Flyway for Python. yoyo runs the SQL; what Flyway does and yoyo does not is built on top, to behave as Flyway behaves, and where Flyway's default was run and found less safe it is not copied. Those judgement calls are in [`DECISIONS-TO-REVIEW.md`](docs/DECISIONS-TO-REVIEW.md).

Java uses Flyway, with its defaults. Here it is [yoyo](https://ollycope.com/software/yoyo/), used
as a library, over the same files - plain SQL named `V<version>__<description>.sql`, run in the
order of their version numbers, and `R__<description>.sql`, run after them and again whenever
they change. yoyo reads the files, splits them into statements and runs them, in a transaction
or, for a file marked `-- transactional: false`, without one. Everything else is in
`ekbatan.migrations`, because yoyo alone does not do it or got it wrong - measured, one at a time.
[`docs/MIGRATOR-LEDGER.md`](docs/MIGRATOR-LEDGER.md) settles every behaviour of a Flyway migrate
against this one, each run on Flyway 12 as well as read in its source.

| yoyo alone | here |
| --- | --- |
| two files with one version both run - a migration's name is its whole file name | refused before any database is touched, by a check a CI test can run (`validate_scripts`) |
| an edited, renamed or deleted applied migration, or a new one older than one applied, is accepted silently | refused before anything runs, as Flyway refuses it; a migration newer than every file is left alone with a warning, as Flyway leaves it |
| file names sorted as text, so `V10` ran before `V2` | version numbers compared as numbers |
| no repeatable migrations | `R__` files, as Flyway runs them: after every versioned migration, in Java's order of their descriptions, again whenever they change |
| a `post-apply` hook runs only when something was applied, so one that failed is never run again | run at the end of every migrate, as Flyway's `afterMigrate` is |
| a migration that fails after MySQL has committed its schema change is simply run again next time, over what it half did | recorded as failed, and the next run refuses until the database has been checked and `repair()` run, as Flyway does |
| nothing stops the first migration running against a database something else built | refused, as Flyway refuses it, until `baseline()` records what is there |
| `V2__x.SQL` handed to Python's import machinery, the suffix check being case-sensitive | read as SQL, as Flyway reads it |
| the lock is a table row: waiters give up after ten seconds, and a killed holder leaves every later deploy failing | a database session lock - advisory lock, `GET_LOCK`, an OS file lock for SQLite - that waiters wait on for as long as it takes and the database drops when its holder dies |
| each migration runs on a second connection, apart from the lock's; when the lock's connection died a second process ran the migration at the same time | the lock, the migration and its history row share one connection and die together |
| a migration is recorded in a separate step after it is applied | recorded in the same transaction wherever the database can roll back a schema change |
| on MySQL and MariaDB, a `transactional: false` migration's statements were never committed, and a savepoint error replaced the real one | committed, and the real error reported |
| query results printed to stdout | logged at `INFO`, switchable, as Flyway's `outputQueryResults` |
| the database's own warnings and notices dropped | logged at `WARNING`, as Flyway logs them |

**Where it differs from Java's Flyway**, as distinct from yoyo:

- **The history table** is `ekbatan_schema_history`, with Flyway's columns but a SHA-256 checksum
  rather than Flyway's CRC32 - which notices two lines joined into one, where Flyway's does not -
  and `installed_on` as UTC text. A database cannot be migrated by the Java port's Flyway and this
  interchangeably.
- **Locations are required.** Flyway defaults to `classpath:db/migration`; there is no classpath
  here to default into.
- **The lock waits for as long as it takes.** Flyway gives up after 50 one-second retries by
  default (`lockRetryCount`), which crashes every replica waiting behind a longer migration.
- **One connection.** Flyway on PostgreSQL, by default, holds its lock in a transaction on one
  connection and runs the migration on another - so a migrator killed mid-statement has its lock
  dropped at once while its statement runs on, and that transaction-held lock also makes
  `CREATE INDEX CONCURRENTLY` wait on it forever. Both measured. Here neither can happen; and on
  PostgreSQL 14 and later a killed migrator's statement is cancelled within a second
  (`client_connection_check_interval`), so the next process does not wait for it to finish.
- **The lock is held for the whole run**, where Flyway takes it once per migration, and the files
  are checked against the history under it, where Flyway checks before taking it. The outcome is
  Flyway's - each migration once, in order.
- **A replica is refused** - `pg_is_in_recovery()`, `read_only`, `super_read_only` - before
  anything else. Flyway migrates whatever the url reaches.
- **A misnamed `.sql` file is refused**, `R1__x.sql` included. Flyway skips it, and it never runs.
- **What yoyo cannot read as Flyway does is refused, not misread**: `DELIMITER`, a Flyway `.conf`
  file, a Flyway callback file, a description longer than the history keeps.
- **`-- transactional: false`** is how a file says it cannot run in a transaction; Flyway has a
  `.conf` file, or detects the statement itself.
- **`baseline` refuses an empty database**, where Flyway baselines it and then never runs its
  first migration.
- **`shards=`** picks a subset of the layout; Java does the same by passing some of `targets(...)`.
- **Not here:** undo migrations, a target version, placeholders, callbacks other than
  `afterMigrate`, migrations in code, and `customize`.

## The Redis lock is Redisson's layout, written with redis-py

> **parity** - Not a divergence any more: the same hash, scripts and unlock message as Redisson, so the ports share one lock.

Java uses Redisson's `RLock`. There is no Redisson for Python, so this port writes what Redisson
writes and runs the scripts Redisson runs, copied from Redisson 3.39.0 (the version Java's build
pins): a hash at the key with one field `<client id>:<thread id>` holding the hold count,
`PEXPIRE` set to `max_hold`, and on release a `0` published on `redisson_lock__channel:{<key>}`. A
waiter subscribes to that channel and retries when a message comes or the holder's TTL runs out,
as Redisson's does, so a bounded wait ends on time and a hand-off is immediate.

Until `EKB-0267` it was `SET NX PX` with a string value. Exclusion held both ways, but a Java
`tryAcquire` on a key Python held failed with `WRONGTYPE` instead of waiting. Run with a Java
and a Python service on one Redis since: each keeps the other out, and a release on either side
wakes a waiter on the other within a second, with a five-minute TTL on the key.

Not copied: Redisson's `WAIT` for replicas on a replicated (non-single-server) setup, and its
sharded `SPUBLISH` in cluster mode. This provider takes one `redis.Redis`, which is the
single-server case.

`redis-py` because it is the only serious candidate that is synchronous (this framework is, and
`coredis` is async-only), ships `py.typed`, and is actively released - 8.1.0 on 2026-07-30 against
`valkey`'s 2025-08-11.

Re-entry is counted the same way in both. Java layers its own `KeyedReentrantHolder` over
Redisson: Redisson's script is reentrant already, but its last-call-wins `leaseTime` would let an
inner re-entry shorten the outer holder's TTL. Here the same holder sits over the same script, so
Redis only ever sees a count of 1, and the outermost `max_hold` governs.

## The lock connection runs in autocommit, as Java's pooled one does

> **parity** - Not a divergence - a bug this port had and fixed, kept because finding the cause took two passes.

Not a divergence - a bug this port had and Java does not, recorded because finding the *cause*
took a second pass and the first pass looked convincing.

The symptom: a `try_acquire` that simply timed out **raised instead of returning None**, which is
the precise confusion the empty-result contract exists to prevent - a caller cannot tell a busy
lock from a broken one if both arrive the same way. `pg_advisory_lock` under a `lock_timeout`
raises `55P03` *and aborts the transaction*, after which Postgres refuses every later statement
on that connection; the `SET lock_timeout = 0` reset sat in a `finally`, ran on the aborted
transaction, and raised `InFailedSqlTransaction`.

The first fix was to keep the reset off that path. It works, and it is the wrong fix: it removes
one statement from an aborted transaction and leaves the next one to find it. The cause is that
this port opened the lock connection through `engine.connect()`, where SQLAlchemy begins a
transaction implicitly. Java borrows a pooled JDBC connection with `autoCommit=true`, so a failed
statement ends its own implicit transaction and leaves nothing behind. An advisory lock is
session-scoped in both Postgres and MySQL, so there was never a transaction to want.

Measured both ways: with the original `finally` restored and `AUTOCOMMIT` added, the suite passes;
the reset is simply no longer running against a poisoned connection. The narrower change had stayed
in, and it was not harmless: skipping the reset after a timed-out wait left the `lock_timeout` on
the pooled connection for its next borrower. The parity scan found it; since `EKB-0263` the reset
runs after every bounded wait, got or not, as in Java.

Two more things came out of reading `PostgresKeyedLockProvider` whole rather than inferring it
from its tests. A connection whose `lock_timeout` reset failed is now **discarded** rather than
returned - Java calls it `dirty` and evicts it, because the next borrower would otherwise inherit
a timeout nobody set. If the wait got the lock, the lock is kept, as in Java, and the connection
is discarded when the lock is released. And a failed `pg_advisory_unlock` invalidates too: the unlock did not
happen, so that session still holds the lock, and handing the connection back to the pool would
hand the lock with it.

## An action with no parameters passes `None`; one with parameters passes an object

> **parity** - Both ports refuse a scalar in the params column, with the same reasoning.

`action_params` is a JSON object column, and both ports refuse to write a scalar or a list into
it: `Action[WalletDepositMoneyAction.Params, Wallet]`, never `Action[str, Wallet]`. Java's message says the same -
"use a record/class params object". A bare `"MXN"` in that column is a row no typed consumer can
read, and `ActionEvent.action_params` is a mapping in both ports.

Where they differ is `None`. Java refuses it too, because an action there always takes a params
object, so a null reaching the check means somebody made a mistake. Here
`executor.execute(SomeAction(), None)` is simply how a parameterless action is invoked, so `None`
is normalised to `{}` rather than refused.

The rule that makes both coherent: `None` means "no parameters" and has an unambiguous
empty-object form; `"MXN"` means "one unnamed parameter" and does not - storing it would mean
inventing a key that no reader on either side knows to look for.

## There are no virtual threads, so each place Java forks one is answered separately

> **forced** - CPython has no equivalent. Five sites, five answers, each recorded.

Java is built around virtual threads and says so on its front page. CPython has nothing
equivalent: a thread is an OS thread, it costs about a megabyte of stack, and the GIL means
more of them buys concurrency on I/O but not parallelism on CPU. So "port the virtual threads"
is not a thing that can be done - each site where Java forks one is its own decision. There are
five in the main sources, and they went five different ways.

**The unit of work itself - no threads either side.** This is the one that matters most and it
needs none. Java binds the open transaction to a `ScopedValue`; here it is a `ContextVar`
holding a shard-to-connection mapping. Neither forks anything; both answer "is this execution
context already inside a transaction on this shard". The model above it is the host's to
choose - a thread per request, a pool, a single thread - and the framework works under any of
them because the state is per-context rather than global. Verified rather than assumed, in
`tests/integration/test_concurrency.py`: eight concurrent units of work take eight distinct
connections, cannot see each other, and a pool worker does not carry a transaction into its
next task.

**Work forked from inside an action - the same rule, stated the same way.** Java's own
documentation tells you to fan out on virtual threads, *join*, and only then touch the plan,
because a plain virtual thread does not inherit a `ScopedValue` binding. A `ContextVar` behaves
identically, and `current_plan()` raises rather than returning `None` so the mistake is loud.

**The keyed-lock watchdog - one thread for all of them, where Java has one each.** Java gives
each outermost hold a `Thread.ofVirtual()` watchdog that sleeps until `maxHold` and then forces
the release. That is the right answer there and measurably so: on a JDK 25 with ten cores, ten
watchdogs fire 17 ms late and *a hundred thousand* fire 48 ms late, because a sleeping virtual
thread is unmounted and is not competing for a carrier at all.

A `threading.Timer` is an OS thread, so the shape does not survive translation. This port used
one per hold until `EKB-0206` and it was worse in every direction at scale - not only heavier
but **later**, because starting a thousand threads takes 70 ms before any of them can fire:

| 1,000 held locks | register | median late | threads | memory |
|---|---|---|---|---|
| a thread per hold | 70 ms | 48 ms | 1000 | 40 MB |
| one watcher and a heap | 3 ms | **6 ms** | **1** | ~0 |

Measured on the real holder after the change: 1,000 held locks register in 2 ms on 1 watchdog
thread and 1 MB; 10,000 in 18 ms, still 1 thread, 8 MB.

It waits until the earliest deadline rather than polling on a cycle. A cycle has to pick an
interval and every choice is wrong in one direction - short burns a core doing nothing, long is
late by up to an interval. Cancelling is a tombstone rather than a search, with a compaction
pass, because nearly every hold closes in time and a heap that only ever grew would be the
whole cost back again.

**This is not worth porting to Java.** The heap is a workaround for a limitation Java does not
have: `Thread.ofVirtual().start(() -> { sleep; release; })` is simpler than a heap and a
scheduler thread, and cancelling it is one interrupt rather than a tombstone to reconcile.

**Job workers - a ceiling rather than a pool, and a knob for it.** Java hands db-scheduler
`Executors.newVirtualThreadPerTaskExecutor()`, so its worker count is unbounded and
`threads(jobs.size())` only sizes the polling batch. Here they are real OS threads, so a ceiling
has to be chosen: `DaemonThreadPool`, defaulting to one per registered job - right, because a
recurring task is a single row and so has at most one execution in flight.
`JobRegistryBuilder.workers(n)` changes it, which Java has no need of and this port does.

The pool starts its workers **on demand** as of `EKB-0207`, which is the half of Java's
`newCachedThreadPool()` that CPython gives for free. Twenty registered jobs with nothing due now
hold no worker threads at all, where they used to hold twenty for the life of the process. The
other half - retiring a thread that has been idle for a minute - is not here and is not in the
standard library either: `ThreadPoolExecutor` grows and never shrinks, and nothing famous in the
ecosystem does it as a thread pool. Checked rather than recalled: Celery's autoscaler shrinks
worker *processes* after a `keepalive` of 30 s, and Twisted's `ThreadPool` has `shrink()` and
`stopAWorker()` that nothing calls automatically.

**A running job holds no connection by itself.** This section said the opposite until `EKB-0207`,
and it was measured wrong rather than reasoned wrong: db-scheduler takes a connection to claim
the row, releases it, runs the body with **none** open, then takes another to write the result -
sampled twelve times during a sleeping job body, the count was zero every time. What holds a
connection is the body's own database work, for as long as that work takes. db-scheduler's own
warning says exactly this and says it conditionally - *"if task bodies hold connections"* - and
the docstring here had dropped the condition.

**Fan-out and dispatch - across shards now parallel, within a shard still not.** Java forks a
virtual thread per shard in `EventFanoutJob.drainOneRound` *and* in `EventHandlingJob`, and a
further one per due notification inside a batch. Two different forks, and they ported
differently.

The per-shard fork ported (`EKB-0203`). Both jobs drain their shards through a
`ThreadPoolExecutor`, one thread per shard, which is worth it for the reason a thread in CPython
usually is not: a batch against a shard is a round trip to a *different* database, so it is I/O
the whole way and the GIL is released for all of it. Measured, one 40 ms round trip per shard:

| shards | sequential | concurrent | speed-up |
|---|---|---|---|
| 1 | 44 ms | 44 ms | 1.0x |
| 4 | 177 ms | 46 ms | 3.9x |
| 8 | 354 ms | 48 ms | 7.3x |
| 16 | 694 ms | 48 ms | 14.6x |
| 32 | 1405 ms | 47 ms | 29.6x |

Flat, as Java's is: a round costs one shard's time however many shards there are. There is no
ceiling and no setting, because Java has neither - it forks one virtual thread per shard and
joins them, and a knob this port has and that one does not is a divergence bought for nothing.
A deployment with many shards already holds a connection pool per shard, so a thread per shard
for the length of a round is in proportion to what it already has. A single shard builds no
pool at all and runs inline, which is most deployments.

The contract is Java's, not just the shape: the whole round is submitted before any failure is
raised, so one unreachable shard does not hold back its siblings, and results come back in
shard order rather than completion order.

**The per-notification fork ported too** (`EKB-0204`). Within one shard a batch's handlers run
at the same time, one thread per notification, bounded by the batch size as Java's is; a batch
of one runs inline. `test_a_batch_invokes_its_handlers_concurrently` pins it: four handlers that
each wait for all four pass only if they run together. Async handlers in a batch run together on
the handler loop.

## The sync API blocks, and blocking is free under gevent and not under asyncio

> **forced** - under asyncio, where a blocking call holds the loop. Under gevent it is as cheap as Java's, for the same reason and with no code here.

Java is built on virtual threads, where a blocking call costs almost nothing: the carrier thread
is released and picked up by other work. That is what lets its whole API be blocking without
apology.

This port's sync API blocks too, and under a threaded server that is exactly right, for the same
reason. Under an **asyncio** server it is
not, because a blocking call there stops the entire event loop rather than one carrier thread.

Measured: a 0.5s framework call made from inside an `async def` handler let a background
heartbeat tick **5 times where a free loop manages about 55**. Every other request on that loop
stalled for the duration.

So under FastAPI, Litestar or any ASGI server, a repository or executor call belongs in a
worker thread::

    wallet = await asyncio.to_thread(wallet_repository.get, wallet_id)
    # or, in Starlette/FastAPI
    wallet = await run_in_threadpool(wallet_repository.get, wallet_id)

Two things make this less painful than it sounds. A plain `def` endpoint in FastAPI is already
run in a threadpool by the framework, so the ordinary way of writing one is safe without any of
the above - `ekbatan-py-examples/fastapi-wallet-rest-uv-pg` is written that way throughout, and
that is why it needs no `to_thread` anywhere. And the ambient transaction lives in a
`ContextVar`, which is per-async-task as well as per-thread, so the state mechanism is already
correct if this port ever grows an async surface.

It has since grown one - see the next section, `ekbatan.aio`. An `async def` handler calling
straight into the *sync* API is still a real mistake with a real cost, and since `EKB-0500` the
sync API refuses it - see the section after next.

### Under gevent this divergence does not exist

This section used to end there, and it was only half the answer. **gevent is what Java's virtual
threads are**, and the framework already runs under it unchanged - because being synchronous is
exactly what makes that possible.

```python
from gevent import monkey; monkey.patch_all()   # before any other import
```

Measured: 50 concurrent `time.sleep(0.5)` finish in **0.51 s** rather than 25, and 20
`getaddrinfo` calls in 0.47 s. `time`, `socket`, `select`, `threading` and `ssl` are all
patched, so *every* blocking call yields - which is the property the greenlet-bridge answer
cannot offer, where only SQLAlchemy's own I/O yields and a `requests.get()` in an action still
freezes the loop while looking asynchronous.

psycopg 3 supports it on purpose. Its `_is_select_patched()` says so - *"Currently supported:
gevent"* - and switches from `wait_c`, which blocks inside C, to `wait_poll`, which does not.

The two mechanisms that could have made this unsafe were run rather than reasoned about: an
open transaction lives in a `ContextVar` and each greenlet keeps its own, and a keyed lock keys
on `threading.get_ident()` which gives 20 greenlets 20 distinct idents. The whole suite passes
unchanged - **631 against 631**, container tests included - and `make test-gevent` is a CI job
so that stays a measurement.

That number was 270 for an hour, because the machine it was first measured on had Docker
stopped, and eleven container tests were written off as "testcontainers dislikes patching".
They pass patched. A failure with an environmental cause is the easiest kind to attribute to
whatever you are currently investigating.

**There is no gevent import in `src/` and there should not be.** The application patches at its
entry point; the framework never knows. What the framework owes is the documentation and the
job that keeps it honest.

It does not reach everyone: gevent serves **WSGI**, so FastAPI and anything else on an asyncio
loop are on `to_thread` or on `ekbatan.aio` - and every example here is FastAPI, so none of them
shows it. The two do not mix: `ekbatan.aio` does not run under gevent's monkey-patching.

## A native async API, `ekbatan.aio`, which Java does not have

> **chosen** - decided by the user (2026-10-18): Java's virtual threads make a blocking API cheap; CPython's asyncio services need an awaited one. See [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md#async-professional).

Java has one API, blocking, and virtual threads make it cheap under any load. CPython has no
virtual threads, and an asyncio service calling the sync API either moves each call to a thread
or stops its event loop. So this port has a second API in `ekbatan.aio`: `AsyncAction` with
`async def perform`, async repositories, an `AsyncActionExecutor` you await, on asyncpg, aiomysql
or aiosqlite. Measured on one PostgreSQL, it does about one and a half times the work of the
sync API in a 40-thread pool once 50 or more requests are in flight.

What a Java service sees does not change. The rows, the outbox rows and their JSON are written by
the same code in both APIs - statements, routing, the optimistic lock, soft delete, the event
rows and the sentinel row are shared, not copied - so a Java consumer cannot tell which API wrote
a row. Event fan-out and dispatch and distributed jobs have no async version: they run on
threads through the sync API, as in Java, and share their tables with Java unchanged. A handler
dispatch runs may be `async def` - it runs on dispatch's handler loop, and its outcomes are
recorded as a sync handler's. The keyed locks have async twins in `ekbatan.aio.locks`, holding
the same keys as the sync providers and Java's, so all three contend for one lock.

Differences inside the async API, each deliberate: a read across shards asks every shard at once
(Java's, like this port's sync one, asks them one after another); an async lock's holder is the
task, where Java's is the thread; and a cancelled wait for an async lock closes its connection.
The full page is `docs/runtime/async.md`.


## Two refusals Java has no need of: after `fork()`, and the sync API on an event loop

> **chosen** - the user's design (2026-10-23): refuse loudly, fix nothing. See [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md#golden-path-and-two-refusals-ekb-0500-).

Java runs one process with virtual threads, so neither case exists there. Python servers fork
their workers (gunicorn, uwsgi, Celery prefork, RQ), and an async server runs every request on
one thread.

- **`ForkedProcessError`**: every object that owns connections or threads - the sync and async
  transaction managers (so every registry), the keyed lock providers, `JobRegistry`,
  `HandlerLoop` - remembers the process that built it and refuses to work in any other. A copy's
  connections are the parent's sockets. Nothing is reset or reconnected.
- **`BlockingCallError`**: the sync API - a transaction, a connection, a repository, the
  executor, a migration run - refuses to run on a thread with a running asyncio loop, where it
  would stop every other task. Nothing is moved to a thread for the caller.

Both say what to do instead; `docs/startup-and-shutdown.md` has the recipe per server.
## The pool knobs are SQLAlchemy's, and three of Hikari's have no counterpart

> **forced** - Different pool libraries expose different knobs. The missing three are in SHORTCOMINGS.md.

Found by diffing Java's public API against this port's definitions, one file at a time, after
the shutdown hook showed that reading a name and assuming a rename is not verification.

`DataSourceConfig` exposes different knobs on each side because the pools underneath are
different objects, not because anything was forgotten - but three of Java's have no equivalent
at all, and an operator moving across should know which:

| Java (Hikari) | here | note |
| --- | --- | --- |
| `jdbcUrl` | `url` | a SQLAlchemy url; as in Java it may not carry the login, which goes in `username` / `password` |
| `dataSourceProperties` | `data_source_properties` | values reach the driver as given rather than as text, and groups and lists are allowed - see below |
| `driverClassName` | the url's `+driver` | `postgresql+psycopg`, not a separate class name |
| `maximumPoolSize` | `pool_size` + `max_overflow` | SQLAlchemy splits the ceiling in two and this port leaves the split visible, because an operator tuning a pool needs to see the knobs their own tooling reports on |
| `minimumIdle` | **none** | `QueuePool` keeps `pool_size` connections and has no separate floor; there is nothing to set |
| `idleTimeout` | **none** | `pool_recycle` is the closest and is *not* the same thing - it caps a connection's total age, not how long it may sit unused |
| `leakDetectionThreshold` | **none** | SQLAlchemy has no borrowed-too-long warning. A connection held past a threshold goes unreported here |
| - | `pool_recycle` | maximum age, which Hikari calls `maxLifetime` and this config does not expose |
| - | `pool_pre_ping` | liveness check on checkout |

The two that cost something are `idleTimeout` and `leakDetectionThreshold`. Neither is a rename
away; both would have to be written. `leakDetectionThreshold` is the one worth arguing about -
a connection leaked inside a unit of work is exactly the failure this framework's transaction
handling exists to prevent, and Java gets a log line for it that this port does not.

## The shutdown hook drains on exit by default, and catches SIGTERM only when asked

> **chosen** - the additive half is on by default as Java's is (`atexit`, `EKB-0221`); catching SIGTERM stays opt-in, because CPython has one handler slot per signal and does not run `atexit` on SIGTERM. The event-loop half was closed in `EKB-0214`. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

**What Java's hook is, in two parts.** A JVM shutdown hook is added to a list, and the JVM runs
that list on its way out - including on SIGTERM, which it turns into an orderly exit. Python has
the first part, `atexit`, and not the second: SIGTERM kills the process and no `atexit` hook runs.
Measured in a real process with a job two seconds into its body:

| how the process ends | default | `stop_on_exit(False)` | `register_shutdown_hook()` |
| --- | --- | --- | --- |
| returns, or raises | **drains** | cut off | drains |
| Ctrl-C | **drains**, exits 130 | cut off | drains |
| SIGTERM | cut off, exits 143 | cut off | **drains**, exits 143 |

So the first part is ported as Java has it - **on by default** since `EKB-0221`, the way
OpenTelemetry's `shutdown_on_exit` and Sentry's flush are - and only the second is opt-in. Of the
libraries read while deciding this, the ones that live in someone else's process (OpenTelemetry,
Sentry, APScheduler) never touch signals, and the ones that do (gunicorn, uvicorn, the Celery, RQ,
Dramatiq and Huey workers) are the process. None of them keeps the handler it replaced; this does.

Two halves of one gap, found by re-reading `JobRegistry.java` whole after noticing its ledger row
had been marked `done` at ledger-creation time rather than by a porting pass.

**The hook - closed in `EKB-0213`, opt-in rather than default.** db-scheduler's own
`SchedulerBuilder.registerShutdownHook` defaults to `false`; Java's `JobRegistry.Builder`
deliberately flips it to `true` and calls it in `build()`, so an ordinary SIGTERM runs
`Scheduler.stop()` and in-flight executions drain. This port had no counterpart at either level,
and now has `JobRegistryBuilder.register_shutdown_hook()`.

**The SIGTERM half is off by default, and that is the divergence that remains.** Java can turn its own on without
asking because a JVM shutdown hook is *additive*: any number can be registered, they all run,
none displaces another. CPython has **one** handler slot per signal and `signal.signal()`
replaces what is there - measured, not assumed - so a library installing one uninvited would be
taking the application's SIGTERM away from it.

Asked for, it behaves. The handler that was there is kept and called afterwards; where there was
none the default is restored and the signal re-raised, so the process still dies of it - a hook
that drained cleanly and left the process alive would be worse than the gap it closed. `stop()`
puts the original back, so a second Ctrl-C reaches the default and exits at once. It refuses off
the main thread, where CPython will not install a handler at all, rather than quietly failing to
install and being discovered during an outage.

**The reason to have it rather than leave it to the application** was in this repository's own
example. `job_worker_pg/__main__.py` carried the six lines an application was supposed to write,
with a docstring arguing the framework should not do this - and those six lines **dropped the
previous handler**. Correct where it stood, since `__main__` had nothing before it; silently
wrong the moment it was copied into a gunicorn or uvicorn worker. If the worked example gets the
subtle half wrong, "leave it to the application" is not a smaller risk than doing it once,
carefully, behind a flag. The example now chains, and says why.

`JobRegistry.stop()` and the `with` block always did the right thing - the gap was only ever
what happens when nobody calls them, which is exactly the container-stop case.

**Off, but not silent** (`EKB-0498`). Java has nothing to warn about, since its hook is on. Here
`start()` logs one warning, once per process, when the hook is off and nothing else in the
process handles SIGTERM, naming the two fixes. It reads the handler and changes nothing; under
uvicorn, whose handler is installed before the lifespan runs, it is quiet.

**The loop - closed in `EKB-0214`.** Java's `EventFanoutJob.execute` is
`while (!shouldStop(ctx))` around `drainOneRound()`, sleeping `pollDelay` between idle rounds:
one execution runs for the life of the process and checks
`ctx.getSchedulerState().isShuttingDown()` each round. Both event jobs here now do the same.

**It was never a decision.** This port's `execute` called `drain_until_idle()` and returned,
letting the `FixedDelay` schedule bring it back, and ignored `ctx` entirely. `git log -S` finds
no commit that ever contained the loop: the commit that created these jobs wrote the
return-and-reschedule form from the start. It was not a restructure of Java's design - Java's
design was not read closely enough when the job was written.

This section used to argue the other form was "defensible on its own", which is the pattern this
file keeps catching: the reflex written first, the justification written afterwards, and nobody
asked to choose. The argument was not even wrong - short executions do free the worker thread and
do fail over in one poll rather than after a dead-execution window - but it was assembled to
explain something that had already happened by accident.

**What the port costs**, and it is the trade Java makes too: a running execution holds its row
`picked` and its worker thread for the life of the process, so an instance that dies is taken
over only after the dead-execution window rather than on the next poll.

One thing is not a straight port. Java cuts its idle sleep short with `Thread.interrupt()`;
CPython has no counterpart, so the sleep is cut into 100 ms slices and the shutdown flag checked
between them. Without that a 30-second poll delay would be 30 seconds of a container waiting to
stop.

## Additions with no Java counterpart

> **chosen** - Additions by definition. Each one is something this port has and Java does not. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Recorded because a reader comparing the two trees will find these and wonder what they map to.

`locks/inprocess.py` was on this list - an `InProcessKeyedLockProvider` Java does not have - and
was removed in `EKB-0225`. A lock in one process's memory excludes nothing in the next worker
process, which is how Python services run, so it kept working and stopped excluding anything
without saying so. The backends are now Java's four: Postgres, MySQL, MariaDB and Redis.
Nothing in Java corresponds to any of them, confirmed by searching the Java for the concept
rather than the name.

| module | what | why there is no Java one |
| --- | --- | --- |
| `accounting.py` | counts statements issued, so "one unit of work is one transaction, N staged rows is one statement" is checkable | Java asserts the same claims through jOOQ's execute listener in its own tests, not through a shipped module |
| `schema.py` | the framework's tables as SQLAlchemy metadata | Java's DDL lives in the Flyway migration SQL; there is no programmatic table definition to port. This is the definition `emit_ddl` generates migrations from, and the one the cross-compatibility scenarios check |
| `codegen.py` | generates typed builders | Java's annotation processor does this at compile time; the processor itself is `n/a` in the ledger |

## Not ported

> **parity** - `event-streaming` runs in a JVM whichever language wrote the row, so there is nothing to port.

`event-streaming`, and not planned. Publishing the outbox to Kafka is a Debezium connector plus
a routing transform, running inside Kafka Connect, in a JVM, regardless of which language wrote
the row. A Python service does not need a Python connector; it needs the same connector pointed
at its database. What it does owe is the schema that connector reads, which is
`ekbatan.schema` and is checked by the conformance suite's cross-compatibility scenarios.

## Driver settings: `data_source_properties` (`EKB-0239`)

> **chosen** - decided with the user on 2026-10-11 after a driver survey: typed values and groups follow how Python drivers take their settings. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java's rules carried over: `user` and `password` are refused in any case, an empty value is
refused, every value is a secret and never printed, and a url carrying a login or a secret is
refused with a message saying where each goes. Where this port differs, and why:

| Java | here | why |
| --- | --- | --- |
| every value turned into text | values handed to the driver as given | JDBC drivers read text; PyMySQL fails on `connect_timeout="10"` and reads `autocommit="false"` as true |
| a nested block or a list refused | allowed | some Python driver settings take one (PyMySQL's `ssl`, `auth_plugin_map`); Java refuses them because Spring splits dotted names into groups, and Python's YAML reader keeps them whole |
| `user`, `password` refused | `user`, `password`, `passwd` refused | PyMySQL reads `passwd` as the password |
| the url's raw text is read | SQLAlchemy's parser, falling back to the text | a SQLAlchemy url comes in one form, and reading it as SQLAlchemy does refuses a setting exactly when it would reach the driver |
| no SQLite | a login on a SQLite config refused | SQLite has none; it would be silently ignored |
| `driver-settings.tsv`, 1,500 settings of every JDBC driver | `tests/driver_settings.tsv`, every setting of psycopg (libpq), PyMySQL and sqlite3 | the drivers this port connects through; see `docs/DRIVER-SURVEY.md` |

## Config files (`EKB-0240`)

> **chosen** - decided with the user on 2026-10-11: no config library, Python's own readers plus PyYAML, each format with its own placeholder style. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java binds the layout through each DI framework - Spring, Quarkus, Micronaut - and gets their
placeholder handling with it. This port has no such framework, so `ekbatan.config_files` reads the
file itself. The layout and its keys are Java's; where it differs:

| Java | here | why |
| --- | --- | --- |
| YAML and `.properties`, through the DI framework | YAML, TOML, JSON and INI, read directly | the formats Python projects use; Python's own readers plus PyYAML |
| `${DB_PASSWORD}`, `${DB_PORT:5432}` | the same in YAML, TOML and JSON; `%(ENV_DB_PASSWORD)s` in INI | INI's own `%(...)s` style, as Supervisor uses it; INI has no default syntax |
| a missing variable fails startup | the same | Spring's fail-fast |
| `jdbc-url` | `url` | a SQLAlchemy url |
| datasource keys `maximum-pool-size`, ... | `pool-size`, ... (`pool_size` too) | the Python pool's own knobs |
| values typed by Jackson | INI text typed by the declared field; in `data-source-properties` by the quote rule | INI holds only text |
| `ekbatan.namespace`, `ekbatan.jobs`, `ekbatan.local-event-handler` bound by the DI framework | read by `load_ekbatan_config` into `EkbatanConfig` | no DI framework; same keys, same defaults |
| unknown `ekbatan.*` keys ignored by Spring's binder | refused, with their path | a misspelt `handling.enable` would otherwise leave handling off without a word |
| `handling.enabled: false` - the handling job bean is not created | your wiring reads it and passes `enabled=` to the job, which then registers with a disabled schedule; the job itself does not read it, as Java's does not (`EKB-0275`) | no conditional beans; the job never runs either way |

The examples follow: each reads an `ekbatan.yaml` beside its `app.py`, holding what the Java
example holds in `application.yml`, and every example with a handler runs it on the fan-out and
handling jobs, switched on there - none delivers events by hand. Job worker threads and the
run-migrations switch stay environment variables - Java has no key for either.

