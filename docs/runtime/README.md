# Runtime

What you see in production, and what to know about running this on CPython.

- **[Concurrency](concurrency.md)** - threads, the GIL, asyncio, gevent, pool sizing, and the
  one way to use this framework wrongly. Under gevent, blocking calls become cheap with no code
  change at all
- **[Native asyncio](async.md)** - `ekbatan.aio`: async repositories, actions and executor on
  asyncpg, aiomysql or aiosqlite; the pool as the cap; shards read at once; what stays sync
- **[Observability](observability.md)** - the span tree, the attributes, the metrics, and what to
  alert on

← [docs index](../README.md)
