# Ledger: behaviour parity with Java, proven by running both

Asked on 2026-10-17, after bug 8: the built-in reads joined the open transaction in Python and
not in Java, while `DIVERGENCE.md` said "identical". The user: "is it possible to methodologically
scan db repository/actions/... frameworks aspects of python and java, to make them more similar?"

## Why the earlier checks missed it

[`JAVA-PARITY-LEDGER.md`](JAVA-PARITY-LEDGER.md) is file-first: every Java file read whole and
ported whole. Reading compares names, signatures and routing. It does not compare **behaviour** -
what a call actually does to the database, what it returns, what it throws, what another
connection sees. Bug 8 had matching names and matching routing (primary, not replica); only the
transaction it ran in differed, and only a run showed that.

## The method: twin runs

For every behaviour a user of the framework can observe:

1. **Name it** from the Java source and Java's tests: one row, one observable behaviour.
2. **Write the same scenario twice** - a temporary JUnit test in a throwaway copy of the Java repo
   and a Python script - each printing `PROBE <key>: <value>` lines.
3. **Run both against the same kind of database** (PostgreSQL unless the behaviour is per dialect)
   and diff the `PROBE` lines.
4. **Record the verdict**: `same` or `differs`, with both outputs. A row is settled only by a run.
   A behaviour that cannot be run (pure API shape) is marked `read` and says why.

The probe sources and their recorded outputs are kept in [`parity-probes/`](../parity-probes/), so
any row can be re-run. The Java tests never stay in the Java repo.

This scan **finds** differences; it changes no framework code. The user decides each one, as with
the Draculas.

## Areas

| # | Area | Java | Python | Findings | State |
|---|---|---|---|---|---|
| A | Repository: reads, writes, versions, soft delete, batches, per dialect | `ekbatan-core` `repository/` | `repository.py` | [A](behaviour-parity/A-repository.md) | done (PG, MySQL, MariaDB): 63 - 46 same, 12 differ, 5 read |
| B | Actions: executor, plan, outbox rows, registry, retries, cross-shard | `ekbatan-core` `action/` | `action/` | [B](behaviour-parity/B-actions.md) | done (PG): 59 - 46 same, 10 differ, 3 read |
| C | Transactions, registry, sharding, config, unknown-shard policy | `ekbatan-core` `persistence/`, `shard/`, `config/` | `persistence.py`, `sharding.py`, `config.py` | [C](behaviour-parity/C-transactions-sharding-config.md) | done (PG): 85 - 56 same, 27 differ, 2 read |
| D | Events: local event handler, handler registry, handling job | `ekbatan-events/local-event-handler` | `events/` | [D](behaviour-parity/D-events.md) | done (PG): 37 - 22 same, 14 differ, 1 read |
| E | Distributed jobs and keyed locks (PG, MySQL, MariaDB, Redis) | `ekbatan-distributed-jobs`, `ekbatan-keyed-lock-redis`, core locks | `distributedjobs/`, `events/jobs.py`, `locks/` | [E](behaviour-parity/E-jobs-locks.md) | done (PG, MySQL, MariaDB, Redis): 53 - 37 same, 14 differ, 2 read |
| F | Migrations: Flyway against the yoyo-based migrator | `ekbatan-flyway` | `migrations.py` | [F](behaviour-parity/F-migrations.md) | done (PG, MySQL, MariaDB): 61 - 29 same, 31 differ, 1 read |
| G | Domain model: models, entities, ids, state, events, JSON, clock | `ekbatan-core` `domain/`, `internal/` | `domain.py`, `ids.py`, `_json.py`, `clock.py` | [G](behaviour-parity/G-domain-model.md) | done: 91 - 51 same, 32 differ, 8 read |

Not in scope, each for a reason: DI integrations (Spring, Quarkus, Micronaut - Python has none),
native-image support, the annotation processor (Python's `codegen` is checked in G only for
generated behaviour), and the Debezium/Kafka streaming modules (not ported).

## Totals

**449 behaviours, all settled by a run in both ports: 287 same, 140 differ, 22 read.**

| Area | Behaviours | Same | Differ | Read |
|---|---|---|---|---|
| A Repository | 63 | 46 | 12 | 5 |
| B Actions | 59 | 46 | 10 | 3 |
| C Transactions, sharding, config | 85 | 56 | 27 | 2 |
| D Events | 37 | 22 | 14 | 1 |
| E Jobs and locks | 53 | 37 | 14 | 2 |
| F Migrations | 61 | 29 | 31 | 1 |
| G Domain model | 91 | 51 | 32 | 8 |

Error class names (`StaleRecordException` against `StaleRecordError`) count as the same when both
ports raise the matching error at the same point. The Differences section of each area file says,
per row, whether Python could match Java and what it would cost. Nothing is fixed yet: the user
decides each one.
