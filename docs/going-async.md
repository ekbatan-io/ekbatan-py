# Going async

Take a wallet service whose endpoints are async def and make its request path natively async with ekbatan.aio: the same models, events and rules, with every database call awaited. Its event handlers and jobs stay as they are, on threads.

This walks through what changes, file by file. The finished service is the
`fastapi-wallet-rest-uv-async-pg` example (and `fastapi-wallet-rest-uv-async-mariadb`), which
passes its own tests; every snippet here is from it. The full reference is
[Native asyncio](runtime/async.md).

## What changes and what does not

| | changes | stays |
| --- | --- | --- |
| model, events, converters, migrations, `schema.py` | | the same files |
| repositories | the async bases; your own queries become `async def` | `to_row` / `from_row`, plain `def` |
| actions on the request path | `AsyncAction`, `async def perform`, awaited reads | what they stage |
| the executor | `AsyncActionExecutor`, awaited | its namespace, retries, cross-shard rule |
| endpoints | `async def`, awaiting | the DTOs, the error mapping |
| event handlers and jobs | | sync, on threads, through the sync API |
| `ekbatan.yaml` | one more pool for the handlers | the urls, the logins, the settings |

## 1. Add the async driver

Beside the sync one: migrations, `schemagen`, the handlers and the jobs still use it.

```sh
uv add "ekbatan[postgres,postgres-async,migrations]"        # PostgreSQL: asyncpg
uv add "ekbatan[mariadb,mariadb-async,migrations]"          # MariaDB: aiomysql (MySQL: mysql-async)
```

## 2. The repositories

The mapping is plain functions, so write it once and give it to both kinds of repository - the
async ones the requests use, and the sync one the handler writes through:

```python
class WalletRows:
    def to_row(self, wallet: Wallet) -> dict[str, Any]: ...
    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet: ...


class WalletRepository(WalletRows, AsyncModelRepository[Wallet, ShardedId[Wallet]]):
    async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return await self.find_where(WALLETS.c.owner_id == owner_id)

    async def total_balance(self) -> Decimal:
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(
            WALLETS.c.state != WalletState.DELETED.name
        )
        total = Decimal(0)
        async for _, conn in self.readonly_dbs():
            total += Decimal(str((await conn.execute(query)).scalar_one()))
        return total


class HandlerAlertRepository(AlertRows, EntityRepository[Alert, Id[Alert]]):
    """The handler runs on a thread, so the one repository it writes through is sync."""
```

Every inherited read and write - `get`, `find_where`, `add`, `update`, `count` - is now a
coroutine, and a read across shards asks them all at once.

## 3. The actions

One line changes in each: `perform` is a coroutine, and it awaits its reads.

```python
class DepositMoneyAction(AsyncAction[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        EmailAddress(params.recipient)
        wallet = await self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))
```

What it stages, and therefore what is committed and which events are written, is unchanged.

## 4. Two registries from one file

The urls in `ekbatan.yaml` keep naming the sync driver. The sync side connects with it; the async
registry reads the same url as naming the database only and connects through its async default.

```python
layout = load_ekbatan_config(config_file).sharding
migrate(layout, MIGRATIONS)                                   # sync, once, at startup

registry = AsyncDatabaseRegistry.from_config(layout)          # requests: asyncpg
executor = (
    AsyncActionExecutor.builder()
    .namespace(ekbatan.namespace)
    .database_registry(registry)
    .repository_registry(RepositoryRegistry([wallets, alerts]))
    .action_registry(ActionRegistry([OpenWalletAction(clock), DepositMoneyAction(wallets, clock)]))
    .event_persister(AsyncSingleTableJsonEventPersister(registry, EVENTS))
    .clock(clock)
    .build()
)

handlers_engine = engine_for(member.config_for("handlersConfig"))   # the handler: psycopg
handler_registry = (
    DatabaseRegistry.builder()
    .with_default_database(TransactionManager(layout.default_shard, handlers_engine))
    .build()
)
```

The handler's executor is the sync `ActionExecutor` over `handler_registry`, with the sync
`RaiseAlertAction`; fan-out and dispatch are built over `handler_registry` too, exactly as in a
sync service. Close the async registry with `await registry.aclose()`.

## 5. The settings file

One pool more, for the handlers, and the session time zone set the documented way - written as
libpq spells it, which asyncpg receives as its own `server_settings`:

```yaml
primaryConfig:                     # requests, on ekbatan.aio
  dataSourceProperties:
    application_name: wallet-service
    options: -c TimeZone=UTC
  url: ${WALLET_DATABASE_URL:postgresql+psycopg://localhost:5432/wallet}
  poolSize: 10
  poolTimeout: 5
handlersConfig:                    # the event handler, on threads
  dataSourceProperties:
    options: -c TimeZone=UTC
  url: ${WALLET_DATABASE_URL:postgresql+psycopg://localhost:5432/wallet}
  poolSize: 5
```

On MySQL and MariaDB the same line is `init_command: SET time_zone = '+00:00'`, which PyMySQL
and aiomysql both read.

## 6. The endpoints

`async def`, and `await` every call into the service. One more error to map: a request that
waited `pool_timeout` for a connection and got none.

```python
@app.post("/wallets/{wallet_id}/deposits", response_model=WalletResponse)
async def deposit(wallet_id: UUID, body: DepositRequest, service: Service) -> WalletResponse:
    return WalletResponse.of(
        await service.executor.execute(
            DepositMoneyAction,
            DepositParams(wallet_id=ShardedId[Wallet](wallet_id), amount=body.amount,
                          recipient=body.recipient),
        )
    )


@app.exception_handler(sqlalchemy.exc.TimeoutError)
async def _too_busy(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "too busy; retry"},
                        headers={"Retry-After": "1"})
```

Never call the sync API from one of these: it would block the event loop for every request.

## 7. Locks: the optimistic lock instead

A keyed lock held across an async unit of work is not provided yet, so the async deposit takes
none. The optimistic lock keeps it correct: a deposit that loses the version check is run again
on a fresh read, once by default, and one that still loses is a `StaleRecordError` - a 409 the
caller retries. Nothing is lost or written twice. Work that must hold a lock belongs in a handler
or a job.

## 8. Run it, with a cap at the door

```sh
uvicorn fastapi_wallet_rest_async_pg.api:create_app --factory --limit-concurrency 50
```

The request pool is the cap on work waiting on the database - ten connections, each request
waiting at most `poolTimeout` for one. `--limit-concurrency` is the cap before that: past 50 open
requests uvicorn answers 503 itself. Keep it a small multiple of the pool, so a burst waits
briefly or is refused quickly.

## 9. Test it

The example's test drives the endpoints with FastAPI's `TestClient`, unchanged. To reach past
HTTP into the service, await on the loop the app runs on:

```python
wallet = client.portal.call(lambda: service.wallets.get(wallet_id))
```

And an action on its own, without a database:

```python
checked = await AsyncActionSpec.of(DepositMoneyAction(wallets, clock)).execute(params)
checked.assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
```

## Next

- [Native asyncio](runtime/async.md) - drivers, the pool, reads across shards, timeouts, and
  what stays sync
- [Concurrency](runtime/concurrency.md) - when threads are just as good
