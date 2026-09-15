# Runtime

What you see in production, and what to know about running this on CPython.

- **[Concurrency](concurrency.md)** - threads, the GIL, asyncio, pool sizing, and the one way to
  use this framework wrongly. **No Java counterpart** - Java's whole answer is "virtual threads"
- **[Observability](observability.md)** - the span tree, the attributes, the metrics, and what to
  alert on

Java has a third page here, on GraalVM native images. CPython has no counterpart and there is
nothing to port.

← [docs index](../README.md)
