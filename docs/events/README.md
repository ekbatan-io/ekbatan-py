# Events out

The framework's job is to persist events safely: the domain rows and the outbox rows commit in
the same transaction. What happens to them afterwards is the application's choice.

Four topologies, and they compose:

1. **In-process only - listen to yourself.** [In-process handlers](local-event-handler.md), for
   when the consumer is part of the same deployment: projections, notifications, audit rows,
   internal workflows, saga steps.
2. **A handler as a broker publisher.** Write a handler and publish to Kafka, Pulsar, RabbitMQ or
   SQS from inside it, with your own client and your own message shape.
3. **CDC straight to a topic.** Point Debezium at the outbox and stream committed rows.
4. **CDC to a raw topic, then route.** One raw stream, fanned out per model, per event type or
   per tenant downstream.

- **[In-process event handlers](local-event-handler.md)** - fan-out and dispatch, retry, expiry,
  idempotency, safe renames
- **[Streaming with Debezium](event-streaming.md)** - the connector, the transform, topic naming

**Every path is at-least-once.** Handlers and consumers must be idempotent - usually by storing
the outbox event id, or a stable business key, before applying any side effect.

← [docs index](../README.md)
