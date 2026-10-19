# Ledger: sync and async as a first-class choice in the docs

Asked on 2026-10-19: make Sync | Async a deliberate choice across the setup guide, the docs, the
website and AGENTS.md. Judgement calls: `docs/DECISIONS-TO-REVIEW.md#syncasync-docs`.

## Steps

| # | Step | State |
|---|---|---|
| 1 | Read AGENTS.md, the setup guide, the pickers, the checker, sync-examples, the async docs and examples | done |
| 2 | Async SQLite example `wallet-service-{uv,pip,poetry}-async-sqlite`; sync-examples, Makefile, mypy, READMEs, docs lists | done |
| 3 | Setup guide: a Mode row in the picker; every step that differs shows the async example's files; SQLite gets real steps; step 19 removed | done |
| 4 | `scripts/check-setup-guide.py` over db x installer x mode (24) | done |
| 5 | `ModeTabs` component; every website code sample that differs by mode gets a Sync / Async switch | done |
| 6 | docs/ copies: the two blocks one after another under Sync / Async labels | done |
| 7 | "Sync or async?" page, docs + website, linked from nav, learn and the setup guide; async reference trimmed; going-async kept or merged | done |
| 8 | AGENTS.md "Two modes" section; stale text updated | done |
| 9 | Verify: make lint, npm build + check, headless browser, checker 24, examples, unit tests | done |

## Page inventory (step 5-6)

Every website page with Python code, and whether a sample differs by mode. Filled as each page is
read whole.

| page | docs copy | switches | stayed single, and why |
|---|---|---|---|
| learn/complete-project-setup | none (the examples are its source) | the Mode row of the picker swaps every step that differs | migrations, model, converters, schemagen, run + verify (server), packaging: identical in both modes |
| learn/sync-or-async (new) | docs/sync-or-async.md (new) | 1 | - |
| reference/action | docs/concepts/actions.md | 4 | `ActionRegistry`, the lifecycle diagram |
| reference/action-plan | (actions.md) | 0 | the plan is one object in both modes; one sentence added |
| reference/action-executor | (actions.md) | 4 | the cross-shard internals snippet |
| learn/actions | docs/concepts/actions.md | 5 (docs: 4; it never had the fan-out sample) | the plan-method sample |
| learn/your-first-action | none | 6 | the event, the model |
| learn/getting-started | docs/getting-started.md (7 pairs; shaped differently) | 2 | the model |
| learn/models-and-entities, reference/model, entity, model-event | docs/concepts/models-and-entities.md | 0 | every sample is the same in both modes; one sentence says so |
| reference/repository | docs/database/repositories.md | 11 | mapping helpers, soft-delete select, registry builder, error text |
| reference/transaction-manager | docs/database/transaction-manager.md | 4 | registry access |
| reference/keyed-locks | docs/database/keyed-locks.md | 5 | the async-only sample, provider table, diagrams |
| reference/sharding | docs/database/sharding.md | 3 | ids, layout config, model, strategy, `migrate` |
| learn/sharding | none | 5 | id layout, model, layout config, compose file |
| learn/sharding-strategies | docs/concepts/sharding.md | 2 (docs: 1) | `ShardedId.generate` |
| learn/sagas | docs/concepts/sagas.md | 2 | the event history |
| learn/outbox | docs/concepts/outbox.md | 0 | `Wallet.deposit`, a model method |
| reference/multi-database, dialects/* | docs/database/{multi-database,postgresql,mysql,mariadb,sqlite}.md | 0 | column helpers, types, UTC settings |
| reference/tables/* | docs/database/tables*.md | 0 | schema helpers |
| reference/events/local-event-handler | docs/events/local-event-handler.md | 3 | HandlerLoop setup (async only), Kafka publisher, external-service handler |
| learn/consuming-events | none | 3 | the Kafka consumer |
| reference/wiring | docs/wiring.md | 8 | Settings, loading the layout, migrations, repository registration |
| reference/wiring-fastapi | docs/wiring-fastapi.md | 3 | error mapping, response model, TestClient |
| learn/wiring-a-service | none | 4 | the `Service` alias |
| learn/install | none | 1 | codegen and schemagen commands |
| learn/going-to-production | none | 0 | wrong/right sample is about mixing; one awaited line added |
| reference/runtime/concurrency | docs/runtime/concurrency.md | 1 | job workers, gevent |
| reference/config-files | docs/database/config-files.md | 2 | - |
| reference/jobs/distributed-jobs, migrations, schemagen | their docs copies | 0 | sync in both modes; one sentence each says so |
| reference/events/streaming | docs/events/event-streaming.md | 0 | consumer-side code |
| reference/runtime/async | docs/runtime/async.md | - | async-only page; trimmed of what moved to Sync or async? |
| learn/async (Going async) | docs/going-async.md | - | kept, reframed as moving a sync service over |

## Checker results (step 4)

| db | installer | sync | async |
|---|---|---|---|
| pg | uv | ok, 52s | ok, 54s |
| pg | pip | ok, 63s | ok, 64s |
| pg | poetry | ok, 59s | ok, 60s |
| mysql | uv | ok, 63s | ok, 63s |
| mysql | pip | ok, 78s | ok, 76s |
| mysql | poetry | ok, 74s | ok, 72s |
| mariadb | uv | ok, 64s | ok, 61s |
| mariadb | pip | ok, 74s | ok, 75s |
| mariadb | poetry | ok, 69s | ok, 67s |
| sqlite | uv | ok, 25s | ok, 26s |
| sqlite | pip | ok, 44s | ok, 33s |
| sqlite | poetry | ok, 32s | ok, 30s |

All 24 pass on the final checker (`scripts/check-setup-guide.py` as committed in EKB-0374).
Run 3 passed the 10 above it that ran before a network outage (pg x 6, mysql uv x 2, sqlite pip
and poetry x 4); run 4 re-ran the other 14 once the network was back, all ok.

Earlier runs, and what they found:

- **Run 1** (all 24): 15 ok. pg/poetry/sync failed importing SQLAlchemy from *this repository's*
  `.venv`: the checker runs under `uv run`, so `VIRTUAL_ENV` pointed there and Poetry installed
  into it, while `uv run` elsewhere re-synced it. mysql/poetry/async and all six MariaDB runs
  then failed with no tables: a uvicorn reloader from the polluted run ignored SIGTERM, stayed on
  port 8000, and answered the next runs' health checks from a database that was gone. Fixed in
  the checker: `unset VIRTUAL_ENV`, the repository's `.venv` off `PATH`, and the service stopped
  until nothing listens on 8000 (TERM, then KILL) before the container is removed.
- **Run 2** (the 9 failures): the stale reloader was still up, same 9 failures; killed by hand.
- **Run 3** (all 24): a DNS outage mid-run (`pypi.org` unresolvable, a MySQL container that
  never came up) failed 14; run 4 re-ran them.
- Planted fault: a wrong outbox row in the SQLite async sample output made the check fail naming
  the missing row.

## Example runs (step 2, 9)

| example | uv | pip | poetry |
|---|---|---|---|
| wallet-service-*-async-sqlite (new) | 18 passed | 18 passed | 18 passed |
| wallet-service-*-sqlite (comments changed) | 19 passed | 19 passed | 19 passed |

`make run` of the async twin narrates the deposit, the alert and five outbox rows. The server
examples' files were not changed, so their runs were not repeated; the checker builds each of
them from the page anyway.

## Browser checks (step 9)

Playwright (Chromium) against the dev server, 2026-10-19, storage cleared first:

- setup guide, default (pg, uv, sync): only `data-mode="sync"` blocks visible; actions step shows
  `DepositMoneyAction(Action[`. Clicked Mode -> Async: only async blocks; `AsyncAction` shown,
  the Makefile shows `--limit-concurrency 50`, the Example row reads
  `fastapi-wallet-rest-uv-async-pg`, storage holds `mode: async`.
- switched to SQLite + Async: `asyncio.run(serve(...))` shown, no `PayStipendJob`.
- navigated to reference/repository: all 11 switches opened on Async (persisted); clicking Sync
  on one flipped all 11 and no awaited code was left visible.
- back on the setup guide: picker shows sqlite / uv / sync, Example row `wallet-service-uv-sqlite`,
  the job step shows `PayStipendJob` - the mode set on the reference page carried back.
- reference/keyed-locks: 5 switches, Async shows `await locks.acquire(...)`; screenshot looked right.

## Findings
