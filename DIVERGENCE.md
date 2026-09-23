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

Two differences remain, and both are forced. The principal is a `str | None` rather than a
`java.security.Principal`, because there is no such interface here and inventing one to hold a
name would be a type for its own sake. And **this port checks that an action holds the
executor's clock**, at construction, refusing one that does not:

```
DepositMoneyAction was built with a different clock than this ActionExecutor.
```

Java cannot make that check and does not need to: a DI container hands the same `Clock` bean to
the action and to the executor. There is no container here - `build()` is a function you write -
so the two are wired separately and can be wired differently. The failure that produces is a
test that froze the executor's clock, asserted on a timestamp the action took from the system
clock, and passed on the machine it was written on.

## An action runs by type or by instance

> **chosen** - Java dispatches only on a class token; this accepts an instance too. An addition to the surface, not a fact about Python. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java dispatches on a class token, so `ActionRegistry` is the only way to reach an action:

```java
executor.execute(principal, DepositMoneyAction.class, params);
```

Both registries are here, with the same builders, and the class form works the same way:

```python
executor.execute(DepositMoney, DepositParams(...))
```

What the registry buys is the same in both ports: the wiring lives in one place, and a caller
holding the executor runs an action without also holding what the action was built with. The
example's handler and its job both reach for `DepositMoney` and know nothing about the wallet
repository it needs.

The instance form stays, because it costs nothing and a test almost always wants the object it
just built:

```python
run = DepositMoney(wallets)
executor.execute(run, DepositParams(...))
```

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
class Wallet(Model[WalletId, WalletState, WalletBuilder]):
```

Leave it off and `copy()` refuses, for the reason the section on the builder gives. Override
`copy()` only to make a copy that deliberately differs - dropping a cache, resetting a counter.

## Identifiers get a named type, not a generic parameter

> **chosen** - Both languages erase, so `Id[Wallet]` would have worked. A named type per model is a convention this port adopted. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java writes `Id<Wallet>` and `ShardedId<Wallet>` and gets erasure at runtime, so
`id.equals(otherId)` compares uuids and nothing else. Python erases the same way, so the same
spelling has the same hole.

The port therefore gives each model a one-line id type:

```python
class WalletId(ShardedId["Wallet"]): ...
```

`WalletId(u) == NotificationId(u)` is then False with no checker involved, and they are two
entries in a set rather than one. `Id[Wallet]` still works and still satisfies a checker; the
named subclass is what makes the interpreter agree.

## `AbstractRepository` is just `Repository`, and is usable as it stands

> **chosen** - Java's four-name hierarchy collapses to fewer here. Structural, and could have been mirrored. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java's hierarchy is four names: a narrow `Repository` interface, an `AbstractRepository`
holding the implementation, and `ModelRepository` / `EntityRepository` on top.

`ModelRepository` and `EntityRepository` are here, and do what they do there: declare which
kind of thing the table holds, and refuse a model whose table cannot hold the timestamps a
model carries.

The other two collapse into one. `AbstractRepository` is abstract in Java because
`fromRecord` and `toRecord` have to be supplied; here they default to mapping by field name, so
the class works as it stands and calling it *Abstract* would be a lie -
`Repository(Wallet, WALLETS, registry)` is a legitimate line. The narrow `Repository` interface
existed to keep `ChangePersister` off the full surface; nothing here needs that yet, and a
Protocol added before anything wants it is a name to maintain for nobody.

## Row mapping is written, as Java's is - but the bridge is not

> **parity** - both ports require every repository to write the mapping. Brought to parity in `EKB-0217`; it defaulted to field name until then, which was the one piece of magic left in the read and write paths.

Java declares both halves abstract, so a repository that has not written them does not compile:

```java
public abstract PERSISTABLE fromRecord(RECORD record);
public abstract RECORD toRecord(PERSISTABLE domainObject);
```

`Repository.to_row` and `from_row` now refuse instead, there being no compile step here. Every
repository in this repository writes both - the seven examples, the suite's fixtures, and the
handful of tests that build one inline.

**Why it changed.** The mapping used to default to field name, and the entry here called that
"this port's largest ergonomic decision". It was also the one place left where something
happened that nobody had written: rename a column without renaming the field and the mapping
silently stopped matching, where Java gets a compile error. A default that is right almost
always is the hardest kind of wrong to find.

**What is still not written is the bridge.** Where the names line up - which is most of the
time - the two methods are one line each:

```python
def to_row(self, wallet: Wallet) -> dict[str, Any]:
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
normalised on the way in - `to_storage` is the analogue of `truncatedTo(MICROS)`, since an aware
value stored as-is would not compare equal to the same instant read back.

For a while this port left both unset and stamped them from the executor's clock when the change
was staged, on the grounds that two lines which are the same every time and wrong occasionally do
not belong in application code. That is a fair argument for a **default**; it was the wrong
argument for dropping the **validation**, and it left one class with two standards for
"required" - `id`, `state` and `version` refused at construction while the two timestamps were
silently optional. Java gets both: a required field, and a factory that supplies it from the
clock. Pass `self.clock.now()`, which is what `clock.instant()` is doing on the other side.

`Entity` has no timestamps in either port. A row that needs to know when it last changed almost
certainly has changes worth telling the rest of the system about, which makes it a `Model`.

The write path matches too, as of `EKB-0194`. **Nothing writes `updated_date` but you.** Java
writes `updatedDate` nowhere in `ekbatan-core` - `ModelRepository` only checks the column exists,
and `nextVersion()` carries whatever the row held forward - and this port briefly refreshed it
from the clock on every staged update. It no longer does:

```python
self.copy().updated_date(self.clock.now()).balance(new).build()
```

on the changes where it means something. The version still moves inside the write, under
`WHERE version = ?`, because that is the optimistic lock rather than a convenience.

The trade is stated plainly: a column named `updated_date` does not move on its own, and a row
that has never been marked still reads as last-updated-when-created. That is the cost of a
framework that does not rewrite a column the application set - which is the property that makes
the column usable for recording when *your* change happened.

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
- **The built-in reads use the primary**, as Java's do. Choosing to accept lag belongs to the
  caller, who is the only one who knows whether they just wrote the row they are about to read.

This was not always true. An earlier version of this port made `readonly_db()` mean "a read" and
default it to the primary, with `replica=True` to opt in - same routing, different name. It was
recorded here as a neutral difference, which was the mistake: it is a judgement call about whose
job consistency is, and it belonged in `docs/DECISIONS-TO-REVIEW.md` where it would have been
argued about rather than filed.

## The connection they hand back has a `rows()` on it

> **chosen** - jOOQ ends a query with the mapping and SQLAlchemy has no such verb, so this port adds one. It could as easily have been left to a comprehension. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

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
return [self.from_row(row) for row in rows]
```

So `Db` supplies the missing one, and the order matches the Java:

```python
return conn.rows(statement).fetch(self.from_row)
```

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
changes for anyone who does not ask.

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

## Handlers are duck-typed

> **forced** - A `Protocol` is how Python spells an interface. No behaviour differs.

Java's `EventHandler` is an interface. Here it is a `Protocol`: anything with `name`,
`event_type` and `handle` is a handler, so an existing class can subscribe without inheriting
anything. `aliases` is optional and read with `getattr`, which is what makes a handler rename
possible without every handler declaring an empty set.

## Names that still differ, and why

> **parity** - A table of names, each differing because the thing differs. No behaviour.

Everything that names the same concept now carries the same name, so a reader moving between the
two ports recognises what they are looking at. What is left differs because the thing itself
differs, not because the name was chosen freely:

| Java | here | why |
| --- | --- | --- |
| `AbstractRepository` | `Repository` | nothing is abstract; see above |
| `Repository` (interface) | - | the narrow interface has no consumer here yet |
| `EventEntity`, `EventEntityRepository` | - | rows are dicts through SQLAlchemy Core, not a mapped class |
| `EventNotification`, `EventNotificationRepository` | - | the same |
| `ModelId`, `Identifiable`, `ShardAwareId` | - | interfaces that exist to name a shape Python reads off the object |
| `ShardedUUID`, `Backoff` | `sharded_uuid()`, `backoff()` | a class holding only static methods is a function here |
| `ShardGroupConfig` + `ShardMemberConfig` | `ShardConfig` | one flat shard list rather than a group/member nesting |
| `Transaction` (package-private) | - | the auto-commit dance SQLAlchemy already does; the public `Transaction` here is the counterpart to the `DSLContext` Java hands the block, not to this |
| `ConnectionProvider` | - | `Engine` is pool, dialect and lifecycle in one |
| `ActionSpec`, `ActionAssert` | `run_action`, `StagedWork` | see above |
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
`ShardingConfig`, `ShardConfig` and `DataSourceConfig`, because that is the inconsistency Java
has and the name is the thing worth matching. `RetryConfig` follows Java rather than the
`RetryPolicy` it was called here.

## A row checks its own invariants, because Python has no constructor to hide them in

> **forced** - A frozen dataclass has no constructor body; `__post_init__` is where the same checks go.

Java's `Model` and `Entity` constructors validate: `id`, `state` and `version` non-null,
`version >= 1`, `events` non-null and copied. `ModelTest` and `EntityTest` pin each one by
message. Those checks are here too, in `__post_init__`, for the same reason - without them the
mistake surfaces as an `IntegrityError` from inside a batched insert, naming a column and a
driver, several frames from the line that made it.

Two things follow from Python that do not follow from Java. A subclass defining its own
`__post_init__` must call `super().__post_init__()`, where Java's constructor chaining is
automatic. And the events tuple is genuinely copied: `events` is annotated as a tuple, nothing
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

## Equality compares the whole row

> **chosen** - **Behavioural and easy to trip over**: two wallets with one id and version but different balances are equal in Java and not here. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java's `Model.equals` compares `id`, `state`, `version`, `createdDate` and `updatedDate`, and
its `hashCode` is the id's alone - so two wallets with the same id and version but different
balances are equal in Java and not here, where a frozen dataclass compares every field. Events
are excluded in both, which is the part that matters: two readers of one row are looking at the
same wallet whether or not one of them is mid-change.

In practice the version moves whenever the content does, so the two agree on every row a
repository produces. Where they differ, this port is the stricter of the two, and a test
asserting `loaded == expected` means here what it looks like it means.

## A shard member carries one datasource pair, not a named map

> **chosen** - Java's `configFor(name)` map is dropped for a fixed pair. A surface removal somebody decided. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java's `ShardMemberConfig` holds a `configs` map with two reserved keys, `primaryConfig` and
`secondaryConfig`, plus any number of user-named entries reached through `configFor(name)`. Its
three DI modules use that to give the job scheduler a pool of its own -
`member.configFor("jobsConfig")` - so the scheduler's polling does not compete with the
application for connections.

Here a shard has a `primary` and an optional `replica`, and nothing else. The map exists in
Java because a DI container binds YAML into it and there is nowhere to hand an object; this
port hands the object: `job_registry().engine(create_engine(...))` gives the scheduler its own
pool directly, which is the same remedy with one less layer. Nothing binds configuration from a
property file here, which is also why `PropertyKeyNormalizer` has no counterpart.

Reopen it if configuration ever does come from a file: the named map is the right shape for
that, and this is the wrong shape for it.

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

## The test-support API is data, not an assertion DSL

> **chosen** - Java ships `ActionSpec`/`ActionAssert`. Returning data instead was a decision, and it is a smaller surface. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java ships `ActionSpec` and `ActionAssert` - 24 fluent assertion methods, and 38 tests of those
methods. `run_action` returns a `StagedWork` instead: `result`, `added(Type)`, `updated(Type)`,
`additions`, `updates`, `staged`, `events`, `emitted(Type)`.

The reason is pytest. `assertAdded(Wallet.class, 2)` exists because a bare `assert` in Java
reports "expected true, got false"; `assert len(staged.added(Wallet)) == 2` already prints both
sides and the list. A DSL here would be a layer between the reader and what actually happened,
and it would need its own 38 tests.

`assertThrows` has no counterpart either - it is `pytest.raises`, which does more.

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
and MariaDB get the predicate as a column, and the suite's own schema is built by an Alembic
revision rather than `create_all` - which is what Java's integration tests do with Flyway, and
is now the only way those indexes are exercised at all.

**None of this was in this file.** `schemagen` is one of the larger design decisions in the port
and had no entry; that the framework's own tables are *built* rather than reflected, where
Java's jOOQ reflects them from the database like any other, had no entry either. Found by being
asked what Java does about `scheduled_tasks`.

## Migrations run on Alembic, with the guard Alembic does not have

> **forced** - There is no Flyway for Python. The checksum guard restores what Flyway has and Alembic lacks.

Java uses Flyway. Here it is Alembic - SQLAlchemy's own migration tool, by the same author -
driven as a library: a `Config` built in memory rather than an `alembic.ini`, so an application
migrates itself with no external binary and no second copy of its connection settings.

Two things sit on top, both because Alembic does not have them and a sharded deployment needs
them. `migrate(config, location)` drives one `Config` per shard, sequentially and fail-fast,
naming the shard that failed and the ones already done. And `ekbatan_revision_checksums` records
a hash of every applied revision, verified before each upgrade.

That second one is not a refinement. Alembic's `alembic_version` holds a single row - the current
head - with no per-migration history and no record of what a revision contained, so editing an
applied revision is accepted in silence. Measured, not assumed: the upgrade ran again without
complaint while the database kept the old columns and the file described new ones. Flyway refuses
that; so does this.

## The Redis lock is `SET NX PX` on redis-py, not Redisson

> **forced** - There is no Redisson for Python. The mechanism underneath it is what this does directly.

Java uses Redisson's `RLock`. There is no Redisson for Python, and the mechanism underneath it is
`SET NX PX` plus a Lua compare-and-delete, which is what this does directly - verified against a
real Redis: mutual exclusion under 32 contending threads, a TTL that expires on its own, and a
release that refuses to delete a key it does not own.

`redis-py` because it is the only serious candidate that is synchronous (this framework is, and
`coredis` is async-only), ships `py.typed`, and is actively released - 8.1.0 on 2026-07-30 against
`valkey`'s 2025-08-11.

Re-entrancy works out the same way for opposite reasons. Java layers its own
`KeyedReentrantHolder` over Redisson to *correct* it: Redisson is reentrant already, but its
last-call-wins `leaseTime` would let an inner re-entry shorten the outer holder's TTL. Here the
same holder *provides* the behaviour, because nothing underneath does. Both make the outermost
`max_hold` the one that governs.

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
the reset is simply no longer running against a poisoned connection. Both changes are in, because
the narrower one is still correct and costs nothing.

Two more things came out of reading `PostgresKeyedLockProvider` whole rather than inferring it
from its tests. A connection whose `lock_timeout` reset failed is now **invalidated** rather than
returned - Java calls it `dirty` and evicts it, because the next borrower would otherwise inherit
a timeout nobody set. And a failed `pg_advisory_unlock` invalidates too: the unlock did not
happen, so that session still holds the lock, and handing the connection back to the pool would
hand the lock with it.

## An action with no parameters passes `None`; one with parameters passes an object

> **parity** - Both ports refuse a scalar in the params column, with the same reasoning.

`action_params` is a JSON object column, and both ports refuse to write a scalar or a list into
it: `Action[DepositParams, Wallet]`, never `Action[str, Wallet]`. Java's message says the same -
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

**The per-notification fork did not port.** Within one shard a batch is still delivered in
order - that half is under "Dispatch delivers one notification at a time", and it is a different
trade, because those handlers are application code running in process rather than one bounded
query per database.

## The framework is synchronous, and blocking is free under gevent and not under asyncio

> **forced** - under asyncio, where a blocking call holds the loop. Under gevent it is as cheap as Java's, for the same reason and with no code here.

Java is built on virtual threads, where a blocking call costs almost nothing: the carrier thread
is released and picked up by other work. That is what lets its whole API be blocking without
apology.

This port is synchronous too - there is no `async` or `await` anywhere in `src/` - and under a
threaded server that is exactly right, for the same reason. Under an **asyncio** server it is
not, because a blocking call there stops the entire event loop rather than one carrier thread.

Measured: a 0.5s framework call made from inside an `async def` handler let a background
heartbeat tick **5 times where a free loop manages about 55**. Every other request on that loop
stalled for the duration.

So under FastAPI, Litestar or any ASGI server, a repository or executor call belongs in a
worker thread::

    wallet = await asyncio.to_thread(wallets.get, wallet_id)
    # or, in Starlette/FastAPI
    wallet = await run_in_threadpool(wallets.get, wallet_id)

Two things make this less painful than it sounds. A plain `def` endpoint in FastAPI is already
run in a threadpool by the framework, so the ordinary way of writing one is safe without any of
the above - `ekbatan-py-examples/fastapi_wallet_rest_pg` is written that way throughout, and
that is why it needs no `to_thread` anywhere. And the ambient transaction lives in a
`ContextVar`, which is per-async-task as well as per-thread, so the state mechanism is already
correct if this port ever grows an async surface.

It has not grown one, and would not gain much by it: SQLAlchemy's async support is a wrapper
over the same drivers, and the framework's value is in what it does between the queries. But an
`async def` handler calling straight into it is a real mistake with a real cost, and nothing in
the API stops you making it.

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
loop are still on `to_thread` - and every example here is FastAPI, so none of them shows it.

## The pool knobs are SQLAlchemy's, and three of Hikari's have no counterpart

> **forced** - Different pool libraries expose different knobs. The missing three are in SHORTCOMINGS.md.

Found by diffing Java's public API against this port's definitions, one file at a time, after
the shutdown hook showed that reading a name and assuming a rename is not verification.

`DataSourceConfig` exposes different knobs on each side because the pools underneath are
different objects, not because anything was forgotten - but three of Java's have no equivalent
at all, and an operator moving across should know which:

| Java (Hikari) | here | note |
| --- | --- | --- |
| `jdbcUrl` | `url` | credentials live in the url here, so `username` / `password` are gone with it |
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

## The shutdown hook is opt-in here and on by default in Java

> **chosen** - the hook exists as of `EKB-0213` and is off until asked for, because CPython has one handler slot per signal where a JVM hook is additive. The event-loop half was closed in `EKB-0214`. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Two halves of one gap, found by re-reading `JobRegistry.java` whole after noticing its ledger row
had been marked `done` at ledger-creation time rather than by a porting pass.

**The hook - closed in `EKB-0213`, opt-in rather than default.** db-scheduler's own
`SchedulerBuilder.registerShutdownHook` defaults to `false`; Java's `JobRegistry.Builder`
deliberately flips it to `true` and calls it in `build()`, so an ordinary SIGTERM runs
`Scheduler.stop()` and in-flight executions drain. This port had no counterpart at either level,
and now has `JobRegistryBuilder.register_shutdown_hook()`.

**Off by default, and that is the divergence that remains.** Java can turn its own on without
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
Nothing in Java corresponds to any of them, confirmed by searching the Java for the concept
rather than the name.

| module | what | why there is no Java one |
| --- | --- | --- |
| `locks/inprocess.py` | `InProcessKeyedLockProvider`, on the same `KeyedReentrantHolder` as the SQL and Redis backends | Java ships only the three SQL providers and the Redis one; a single-instance deployment there is expected to take a SQL lock against the database it already has |
| `accounting.py` | counts statements issued, so "one unit of work is one transaction, N staged rows is one statement" is checkable | Java asserts the same claims through jOOQ's execute listener in its own tests, not through a shipped module |
| `schema.py` | the framework's tables as SQLAlchemy metadata | Java's DDL lives in the Flyway migration SQL; there is no programmatic table definition to port. This is the definition Alembic generates from, and the one the cross-compatibility scenarios check |
| `codegen.py` | generates typed builders | Java's annotation processor does this at compile time; the processor itself is `n/a` in the ledger |

## Not ported

> **parity** - `event-streaming` runs in a JVM whichever language wrote the row, so there is nothing to port.

`event-streaming`, and not planned. Publishing the outbox to Kafka is a Debezium connector plus
a routing transform, running inside Kafka Connect, in a JVM, regardless of which language wrote
the row. A Python service does not need a Python connector; it needs the same connector pointed
at its database. What it does owe is the schema that connector reads, which is
`ekbatan.schema` and is checked by the conformance suite's cross-compatibility scenarios.
