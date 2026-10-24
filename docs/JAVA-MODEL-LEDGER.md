# Examples follow the Java model - ledger

The brief (the user, 2026-10-24): "i want the all the example in the project to be fixed ...
follow java model"; "the naming conventions of the two sides should be similar"; "many places we
write wallets, instead of wallet_repository. i like repository naming convention more, so also
fix this matter throughout the whole project and examples"; the complete-project-setup guide
"more similar to java regarding the example codes ... there are something that are java specific
and there are something that are python specific, i'm not insisting on them to be unified, but
the rest yes"; remove the guide's test, keep the wiring and Package it, make the HTTP layer much
smaller; AGENTS.md and agent-skills/ carry the new names everywhere.

The model for each example is the matching Java one:

| Python | Java |
| --- | --- |
| `fastapi-wallet-rest-*` (pg, mysql, mariadb; sync and async) | `spring-boot-wallet-rest-*` |
| `fastapi-wallet-rest-*-sharded-pg` | `spring-boot-wallet-rest-*-sharded-pg` |
| `fastapi-wallet-saga-*` | `spring-boot-wallet-saga-pg` |
| `job-worker-*` | `spring-boot-job-worker-pg` |
| `wallet-service-*-sqlite` | none - the wallet-rest pieces and names |
| the complete-project-setup guide | Java's guide (`website/src/pages/learn/complete-project-setup.mdx`) |

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "Examples follow the Java model (EKB-0529-)".
Status: `todo` / `doing` / `done` / `n/a`. A row is `done` only when a run shows it.

## Survey

| # | item | status | proof |
| --- | --- | --- | --- |
| S1 | Java's REST, sharded, saga and job-worker examples read (code, config, compose, tests) | done | every Java REST example is two shards (global, mexico) on two servers, `sharded-pg` the same code as `pg`; the job worker too; the saga is one database with `Id<Wallet>` |
| S2 | Java's guide read, and where it disagrees with Java's examples | done | the guide: one shard, `Id<Wallet>`, no country code, a `WalletStipendJob` the REST example lacks, a handler calling a `CreateNotificationAction` it never shows, `NUMERIC(20,4)` where the examples have `DECIMAL(19,4)` |
| S3 | every place the old names and `wallets`-style repository variables appear | done | 87 files, ~700 hits outside the examples: docs and website pages, `README.md`, `DIVERGENCE.md`, `AGENTS.md`, agent-skills, parity probes; history files (ledgers, DECISIONS-TO-REVIEW, the dracula detail files, parity-round-2) are records and keep their names |

## Examples

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | wallet-rest, sync (pg, mysql, mariadb, sharded-pg): Java's pieces and names | done | two shards on two servers, Java's model, actions, handler, endpoints (`/wallets`, `/{id}/deposits`, `/{id}/close`, `GET /{id}`), camelCase JSON; `make -C` 6 passed each (pg, mysql, mariadb, sharded-pg) |
| E2 | wallet-rest, async (pg, mysql, mariadb, sharded-pg) | done | the same on `ekbatan.aio`, the handler on its own loop over `handlersConfig`; `make -C` 6 passed each |
| E3 | job-worker, sync and async | done | `WalletStipendJob` (2 s, below 100.00 gets 10.00, under the keyed lock, a failed deposit skipped) and `WalletReportJob` (5 s); two shards; no seeding, as Java; `make -C` 4 passed each |
| E4 | saga, sync and async | done | one database and `Id[Wallet]`, as Java's saga; `POST /wallets/transfers` 202; `TransferStepName`, `exists_step`; `make -C` 6 passed each |
| E5 | wallet-service SQLite, sync and async | done | the REST pieces, two SQLite files; the script's tour without the stipend; `make -C` 4 passed each |
| E6 | each example's tests check what the Java example's tests check | done | Java's cases, by Java's names; kept beside them: no service object shows the password, the migration files pass the checks a deploy runs |
| E7 | `scripts/sync-examples.py`: compose, forced types; pip and Poetry copies written, `--check` | done | one forced type (`Currency`; the saga adds `TransferStepNameConverter`), two servers per two-shard example (5432/5433, 3306/3307), `compose-init.sql` removed, the build section says two databases; `--check` in step |
| E8 | `schema.py` and `builders.py` regenerated | done | builders by `ekbatan.codegen`, formatted as `check-builders.py` compares (15 checked, 0 wrong); `schema.py` by each example's `make` |

## The project

| # | item | status | proof |
| --- | --- | --- | --- |
| P1 | framework test fixtures named as Java names its wallet classes | done | `WalletCreateAction`, `WalletDepositMoneyAction`, `WalletCloseAction`, `CreateNotificationAction`, `WalletStipendJob`, `WalletReportJob`, `WalletMoneyDepositedEventHandler`; Params nested, the async twins carry them; unit 2115 passed, SQLite 3220 passed; postgres/mysql/mariadb 5778 passed - the 14 failures all in `test_setup_guide.py`, whose page still imports the old example files (G) |
| P2 | repository variables are Java's field names in snake_case (`wallet_repository`), in tests, `src/` docstrings and comments | done | tests: as identifiers only (strings and a `Table` named `wallets` kept), plus `repo`/`notes`/`repository` locals; `src/` docstrings and the outbox's error text; runs as P1 |
| P3 | docs/ and website pages show the new names | done | 66 files renamed mechanically, Params nested in 16 snippet files, the notification, entity, alias, async, sharding and model snippets by hand; links, divergence, check-api (546 snippets, 0 wrong), docs-website-sync (34 pairs), `npm run build` and `npm run check` green |
| P4 | AGENTS.md: every mention, the naming table, a new repository-variable row | done | the table now has actions, `Params`, events, handlers, jobs, models, repositories and repository variables in Java's names, and the rule "copy Java's names exactly" |
| P5 | agent-skills/: text and examples | done | the examples skill and its matrix; mirrored into `.claude/skills` and `.agents/skills` by `agent-skills/scripts/sync.sh` |
| P6 | parity probes | done | 14 files: class names and repository variables; each still parses; recorded `.out` files untouched |
| P7 | `DRACULAS.md` #29 | done | marked matched; what stays Python's: `balance` as text, FastAPI's 422 |

## The guide

| # | item | status | proof |
| --- | --- | --- | --- |
| G1 | the page's example code follows Java's guide's, apart from what is Python's | done | `website/src/setup-guide/`: one shard, `Id[Wallet]`, Java's two actions, handler and stipend job, plus what makes it run; `test_setup_guide.py` rewritten (26 passed) |
| G2 | "The test" removed; the wiring and Package it kept | done | the step, `make test`, pytest and testcontainers gone from the page |
| G3 | the HTTP layer: one small file, three endpoints; the SQLite entry point likewise | done | `api.py`: create, deposit, notifications (91 lines, sync and async); the SQLite script 65 lines |
| G4 | `scripts/check-setup-guide.py` verifies through the endpoints and the outbox | done | waits on `/docs`, reads `/wallets/{id}/notifications`, checks the `notifications` table and the outbox rows the page lists; SQLite reads the script's `MONEY_DEPOSITED` line |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| R1 | unit, sqlite, postgres/mysql/mariadb, psycopg async, gevent | done | unit 2097 passed; the whole suite on SQLite 3202 passed, 109 skipped; postgres, mysql, mariadb 5774 passed, 48 skipped; `make test-psycopg-async` 1336 passed; `make test-gevent` 2823 passed, 488 skipped (the async runs); all exit 0 |
| R2 | ruff, mypy, links, divergence, builders, sync, check-api, docs-website-sync, site build and check | done | ruff and ruff format over `make lint`'s scope, now with `website/src/setup-guide` (733 files); mypy 224 files; links, divergence, check-builders (15), `sync-examples --check`, check-api (546 snippets, 0 wrong), docs-website-sync (34 pairs); `npm run build` (59 pages) and `npm run check` exit 0 |
| R3 | the 28 uv and pip examples, both SQLite `make run`s | done | 28 of 28 (`make -C`, each its wheel installed): REST 6 passed each, saga 6, job worker 4, SQLite 4; both SQLite `make run`s exit 0. The 14 Poetry copies not run - Poetry is not installed here; their `src/` is the uv projects', byte for byte (`sync-examples.py --check`) |
| R4 | the setup-guide check, every database, uv and pip, sync and async | done | 16 of 16 ok - PostgreSQL, MySQL, MariaDB and SQLite, each with uv and pip, sync and async; ports 5432/3306/8000 free first; Poetry not run (not installed) |
| R5 | Poetry, once it was installed (Poetry 2.5.1, Homebrew): the 14 Poetry examples and the setup-guide check | done | on `5441dec`: each Poetry example from scratch (`make clean`, then `make`: a new Poetry environment, `schema.py` regenerated in Docker, the wheel built and its tests run installed) - 14 of 14, REST 6 passed each, saga 6, job worker 4, SQLite 4 - and both SQLite `make run`s exit 0; `check-setup-guide.py --install poetry` 8 of 8 ok (PostgreSQL, MySQL, MariaDB, SQLite, sync and async). The one warning in the REST and saga runs is Starlette's `httpx` deprecation from the test client, the same in the uv copies |

## Commits

| # | item | status | proof |
| --- | --- | --- | --- |
| C1 | committed on main from EKB-0529, one line each | done | EKB-0529 suite, fixtures, `src/` docstrings, probes; EKB-0530 examples and `sync-examples.py`; EKB-0531 the guide, its check and test; EKB-0532 docs, website, AGENTS.md, skills, Draculas, this ledger, decisions |
