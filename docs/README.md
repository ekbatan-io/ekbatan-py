# Documentation

Linked from the top-level [README](../README.md). Each section has its own index.

**Start here**

- **[Concepts](concepts/README.md)** - the dual-write trap, the outbox, actions, models and
  entities, sagas, sharding
- **[Getting started](getting-started.md)** - install, the extras, the generators, your first
  action

**Building an application**

- **[Wiring](wiring.md)** - the complete bootstrap, block by block
- **[Wiring with FastAPI](wiring-fastapi.md)** - the lifespan, the dependency, and three sharp
  traps
- **[Database](database/README.md)** - the framework's tables, repositories, transactions, keyed
  locks, sharding, migrations, the four dialects, schemagen
- **[Events out](events/README.md)** - in-process handlers, and Debezium
- **[Jobs](jobs/README.md)** - cluster-exclusive background work

**Running it**

- **[Concurrency](runtime/concurrency.md)** - threads, the GIL, asyncio, gevent, and pool sizing
- **[Observability](runtime/observability.md)** - spans, attributes, metrics, what to alert on
- **[Running beside a Java service](beside-java.md)** - sharing tables, locks, events and ids
  with a Java service
- **[Releasing](../RELEASE.md)** - how a release is cut

**About the port**

- **[DIVERGENCE.md](../DIVERGENCE.md)** - every deliberate difference from the Java port
- **[SHORTCOMINGS.md](SHORTCOMINGS.md)** - where this port is behind, and by how much
- **[DECISIONS-TO-REVIEW.md](DECISIONS-TO-REVIEW.md)** - decisions worth arguing about, and two
  still open
- **[JAVA-PARITY-LEDGER.md](JAVA-PARITY-LEDGER.md)** - every Java source file, and where it went
- **[BEYOND-CODE-LEDGER.md](BEYOND-CODE-LEDGER.md)** - the same discipline for everything else
- **[DOCS-PARITY.md](DOCS-PARITY.md)** - how far this tree has drifted from the Java port's docs,
  and what is capped

For conventions and how to work in this repository, see [AGENTS.md](../AGENTS.md).
