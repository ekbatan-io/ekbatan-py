# Core concepts

The pieces every user of this framework touches. Read them in this order.

- **[The dual-write trap](the-dual-write-trap.md)** - the bug the whole framework exists to make
  unwritable
- **[The outbox](outbox.md)** - state and events in one transaction, and the two ways to consume
  them
- **[Actions](actions.md)** - the two-phase lifecycle, retries, why nesting is refused
- **[Models and entities](models-and-entities.md)** - which to use, immutability, optimistic
  locking
- **[Sagas](sagas.md)** - workflows that will not fit in one transaction, and honest compensation
- **[Sharding](sharding.md)** - two axes, three strategies, and why cross-shard is refused by
  default

These ideas do not depend on the language. A Python service and a Java service built on Ekbatan
for Java can even share the same tables - see
[Running beside a Java service](../beside-java.md).

For wiring and operations rather than ideas, see [Getting started](../getting-started.md),
[Wiring](../wiring.md) and [Concurrency](../runtime/concurrency.md).
