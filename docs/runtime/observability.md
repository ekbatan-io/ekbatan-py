# Observability

What the framework reports through **OpenTelemetry** - spans, their attributes, and metrics - and
the names of its loggers. It depends on the OpenTelemetry API only, not the SDK. With no SDK
installed, every call does nothing and costs nothing; install one to collect the data.

## Bringing your own SDK

Install an SDK and an exporter:

```
uv add opentelemetry-sdk opentelemetry-exporter-otlp
```

Configure them through the standard environment variables, such as
`OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_SERVICE_NAME`. The framework uses whatever the global
provider returns, for both traces and metrics.

## Span hierarchy

Each `executor.execute(...)` produces:

```
[ekbatan.action.execute]              the whole call
├── [ekbatan.action.perform]          your perform()
└── [ekbatan.action.persist]          the write phase
    └── [ekbatan.transaction]         one per shard
        ├── [ekbatan.repository]      the batched write
        └── [ekbatan.event.persist]   the outbox rows
```

A single-shard action gives one of each. A cross-shard action gives one transaction span, with
its repository and event spans inside it, **for each shard it touches**.

**Every `transaction()` block is a span**, a nested one too, because a nested block is a
transaction of its own. A repository call that joins the open transaction starts nothing new, so
it makes no transaction span. See [the transaction manager](../database/transaction-manager.md#do-not-nest).

## Attributes

| attribute | type | span | meaning |
| --- | --- | --- | --- |
| `ekbatan.action.name` | string | action.execute | the action class's name |
| `ekbatan.action.principal` | string | action.execute | who asked |
| `ekbatan.action.outcome` | string | action.execute | `success` or `error` |
| `ekbatan.action.retry.count` | int | action.execute | total retries; 0 if none |
| `ekbatan.shard.cross_shard` | bool | action.persist | present and true when changes span shards |
| `ekbatan.shard.group` | int | transaction | the shard's group |
| `ekbatan.shard.member` | int | transaction | the shard's member |
| `db.operation.name` | string | repository | `BATCH_INSERT` or `BATCH_UPDATE` |
| `ekbatan.entity.type` | string | repository | the domain class's name |
| `ekbatan.batch.size` | int | repository | rows in the batch |
| `ekbatan.event.count` | int | event.persist | events written |

`db.operation.name` is OpenTelemetry's standard attribute name, with the framework's own values:
the executor always writes in batches. Every other attribute is under `ekbatan.*`.

### Attributes on a partial cross-shard commit

When a cross-shard action commits some shards and then fails on another, the failing span gets:

| attribute | meaning |
| --- | --- |
| `ekbatan.shard.partial_commit_failure` | that this happened |
| `ekbatan.shard.committed_shards` | which shards are already committed and cannot be undone |
| `ekbatan.shard.failed_shard` | which one did not |

The same facts are logged at ERROR, on a line starting `CRITICAL:`. Having them on the span too
means the trace that shows the failure also shows what state the data was left in - the first
thing anyone reading that trace needs to know.

### The instrumentation scope

The scope - the name the spans are reported under - is `io.ekbatan.core`, with the installed
package's version. The meter is `io.ekbatan.events.localeventhandler`.

If you also run a service built on Ekbatan for Java, it reports the same span, attribute and
metric names under the same scope. So a filter on `ekbatan.action.name`, a dashboard or an alert
covers both services - see [Running beside a Java service](../beside-java.md#traces-and-logs).

## Loggers

The loggers are standard `logging` loggers under `io.ekbatan`, so one setting covers them all:
`logging.getLogger("io.ekbatan").setLevel(logging.WARNING)`.

The names look like Java class names, not Python modules, on purpose: a service built on Ekbatan
for Java logs the same lines under the same names, so one logging setup covers both - see
[Running beside a Java service](../beside-java.md#traces-and-logs).

| logger | logs |
| --- | --- |
| `io.ekbatan.core.action.ActionExecutor` | each action: executing, completed, failed; cross-shard warnings |
| `io.ekbatan.core.action.Retry` | each retry |
| `io.ekbatan.core.action.persister.ChangePersister` | what a persist wrote |
| `io.ekbatan.core.persistence.TransactionManager` | transactions started, committed, rolled back |
| `io.ekbatan.core.shard.DatabaseRegistry` | a shard routed to the default; a pool that failed to close |
| `io.ekbatan.core.concurrent.KeyedReentrantHolder` | a lock released at its hold limit |
| `io.ekbatan.core.concurrent.PostgresKeyedLockProvider`, `...MySQLKeyedLockProvider`, `...MariaDBKeyedLockProvider` | a lock that failed to release |
| `io.ekbatan.keyedlock.redis.RedisKeyedLockProvider` | the same, for Redis |
| `io.ekbatan.distributedjobs.JobRegistry` | jobs started, finished, failed; the registry starting and stopping |
| `io.ekbatan.events.localeventhandler.job.EventFanoutJob` | fan-out |
| `io.ekbatan.events.localeventhandler.job.EventHandlingJob` | handler failures and discarded notifications |
| `io.ekbatan.flyway.FlywayMigrator` | migrations |

While a database is being migrated, each line the migrator logs carries two extra attributes on
its log record: `ekbatanShard` (e.g. `1:0`) and `ekbatanTarget` (e.g. `mexico/member-0`). To
show one, write `%(ekbatanShard)s` in a format string, and give the formatter
`defaults={"ekbatanShard": "", "ekbatanTarget": ""}` for the lines logged outside a migration.
Only the migrator's own lines carry them, not lines a driver logs in the meantime.

## Retry events

Each retry adds a span **event** named `retry` to the action span, with two attributes:
`retry.count`, and `retry.exception` - the class name of the exception that matched, which may
have been found in the cause chain.

Retries are not spans of their own. Each retry runs perform and persist again, so the child spans
of every attempt already appear under the action span.

## Errors

A failing span is marked `ERROR` and the exception is recorded on it.

## Context propagation

The current span is kept in a `ContextVar`, like the open transaction: one value per thread
**and** per asyncio task. So traces follow the work under a threaded server and an asyncio one
alike. Work handed to a thread pool from inside a unit of work does not inherit it - the same rule
as for the transaction. See [Concurrency](concurrency.md).

## Metrics

Metrics come from the **event pipeline only**. Actions are traced, not counted: the core emits
spans and no metrics.

| instrument | type | unit | tags | from |
| --- | --- | --- | --- | --- |
| `ekbatan.events.handled` | counter | `{notification}` | `outcome`, `handler` | dispatch |
| `ekbatan.events.handler.duration` | histogram | `s` | `handler`, `outcome` | dispatch |
| `ekbatan.events.delivery.lag` | histogram | `s` | `handler` | dispatch |
| `ekbatan.events.fanned_out` | counter | `{event}` | - | fan-out |
| `ekbatan.events.notifications_created` | counter | `{notification}` | - | fan-out |

### What each one answers

**`handled`** gives throughput and failure rate. The `handler` tag tells one broken handler apart
from a general problem.

**`handler.duration`** shows which handler is *slow*, as opposed to failing. A handler going from
5 ms to 5 s shows here long before it starts raising.

**`delivery.lag`** shows how far behind delivery is running, and it is the one to alert on. A
shard falling behind still reports healthy success counts, so **no counter can show a growing
backlog.** It is recorded on success only, so it measures the time to deliver, not time spent
failing.

### `outcome` values

| value | meaning |
| --- | --- |
| `succeeded` | the handler returned |
| `failed_retry` | it raised; a retry is scheduled with backoff |
| `expired_preflight` | the row was already past the retention window when claimed - the handler never ran |
| `expired_postfailure` | it raised, and the next retry would land past the deadline |

`handler.duration` uses a narrower `outcome`: just `succeeded` or `failed`. When a handler
returns, the only known fact is whether it raised. Whether a failure becomes a retry or an expiry
is decided afterwards, against the clock at that moment. `expired_preflight` never appears in the
histogram, because no handler ran.

### What to alert on

**`delivery.lag` rising steadily** is the one that matters most, for the reason above.

**`handled{outcome="expired_postfailure"}` above zero means events were discarded.** A
notification reaches that state only after failing for the whole retention window - seven days by
default - so each one is a delivery that was given up for good. Each is also logged at ERROR with
the handler name and an example event id.

**A steady stream of `failed_retry` on a single `handler`** usually means that handler cannot
succeed at all. The classic case is an `ImportError` from a missing optional dependency, which no
amount of retrying fixes. An alert on this catches it in minutes; the discard alert above fires
only a week later, once the events are gone.

### Known caveat

`ekbatan.events.fanned_out` counts rows **read**, not rows written. On a deployment with a read
replica that lags behind, a round can re-read events it has already delivered, so the counter
over-reports. Treat it as an upper bound.

## Read-path tracing is not instrumented

A repository's reads produce no spans of their own. They run inside `perform()`, which already has
one, and a span for every lookup would be noise in an action that does several. Add spans in your
own repository method if you need them.

## See also

- [Actions](../concepts/actions.md) - the source of the three action spans
- [The transaction manager](../database/transaction-manager.md) - what `ekbatan.transaction` wraps
- [In-process event handlers](../events/local-event-handler.md) - what emits the metrics
