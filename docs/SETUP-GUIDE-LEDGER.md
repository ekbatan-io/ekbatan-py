# Ledger: the setup guide runs as written (Dracula bug 6)

Asked on 2026-10-17: fix `website/src/pages/learn/complete-project-setup.mdx` and
`docs/getting-started.md` so that a reader who follows them, in order, in an empty folder, gets a
working project - and prove it by doing exactly that.

**Known limit:** `ekbatan` is not on PyPI yet (Dracula 20), so `uv add ekbatan[...]` cannot work
for anyone until it is published. The walkthrough installs it from this checkout instead and
changes nothing else; the guide keeps the PyPI command.

## Steps

| # | Step | State |
|---|---|---|
| 1 | Read the guide end to end; list every command and every file it has the reader write | done |
| 2 | Re-check the sweep's six findings against the page as it is now | done - all six still there, and more |
| 3 | Fix the page | done - rewritten on the examples' files |
| 4 | Walk through it in an empty folder: every command, every file, as written | done - `scripts/check-setup-guide.py`, 9 of 9 |
| 5 | The same for `docs/getting-started.md` | done - quotes, `*.sql`, method names; and every other unquoted install in docs and site |
| 6 | Record: DRACULAS, the detail file | done |

## Findings

### The page as it was (step 1-2)

The sweep's six are all still there, and the page's code had drifted much further: it is excerpts
with no imports, using names it never defines (`Settings`, `clock`, `MIGRATIONS`, `layout`); calls
that do not match their own definitions (`OpenWalletAction(clock)` against
`__init__(wallets, clock)`, `Wallet.open(balance=...)` against `opening_balance=`,
`PayStipendJob(executor, wallets)` against `(wallets, executor)`); a `RaiseAlertAction` never
shown; `lockConfig` used and missing from the yaml; a `mysql_init.sql` "from step 2" that step 2
never mentions; only a PostgreSQL `docker run`; and a final check promising both outbox rows
`delivered = true`, where an event no handler subscribes to stays undelivered.

### Decided with the user (2026-10-17)

**Full files, kept in sync.** Every file the page has the reader write is the complete file of
the matching example project, rendered from that file when the site is built
(`import x from ".../model.py?raw"`) - so the page cannot drift from the example, which is tested
on every commit. Proven by building the project from the page in an empty folder and running it.

### Design (built, not asked)

- The page follows the examples: `fastapi_wallet_rest_pg`, `_mysql`, `_mariadb`, one per picker
  choice. `model.py`, `actions.py`, `handlers.py`, `jobs.py` and `api.py` are identical across the
  three and shown once; `repository.py`, `app.py`, `ekbatan.yaml`, the migrations and the test
  differ and are shown per database.
- The project layout is the examples': migrations and `ekbatan.yaml` **inside** the package, so a
  wheel carries them (the sweep's other point about the old layout).
- The examples' migrations are split as Java's examples split theirs - `V0001__eventlog.sql`,
  `V0002__scheduled_tasks.sql`, `V0003__wallets.sql`, `V0004__alerts.sql` (Java's
  `V0004__notifications.sql`; the table is `alerts` here) - which is also what the page already
  recommended. They were one file.
- The few files that name the example's own package are not copied: `builders.py` is generated
  by the page's `codegen` command, `schema.py` by its `schemagen` command, and `__main__.py` is
  replaced by the Makefile's `uvicorn --factory`.
- SQLite: no FastAPI example exists for it, and its example (`wallet_service_sqlite`) has a
  different shape - no HTTP, no migrations, a hand-written schema. The SQLite choice points there
  rather than pretending this page builds it.

### Walkthrough 1: uv, PostgreSQL (2026-10-17)

In an empty folder, uv 0.13.0, Docker:

- `uv init wallet` creates `src/wallet/` with the `uv_build` backend (so does `poetry new`). The
  page's `wallet/wallet/` tree was the wrong half, not the command.
- Unquoted `ekbatan[postgres,migrations]` in zsh: `no matches found`. Quoted, it installs.
- **A framework bug the guide had hidden:** `python -m ekbatan.codegen wallet.model` cannot run in a
  new project. `model.py` imports `builders.py` - the file the command writes - so the first run
  fails on the import of its own output, and under a `src/` layout `--out src/wallet/builders.py`
  also inferred the package `src.wallet`. The examples never met it because their builders are
  committed. Fixed in `codegen`: the package comes from the module being read, and while the
  builders module does not exist yet an empty stand-in answers its imports. The output for the
  example matches its committed `builders.py`, package renamed, formatting aside.
- With the example's files copied in and the page's Makefile: `make test` generated `schema.py`
  and `builders.py` and passed (11). `make run` served; a wallet opened, a deposit landed, the
  handler's alert arrived; `docker exec wallet-pg psql ... '\dt'` lists `alerts`,
  `ekbatan_schema_history`, `event_notifications`, `events`, `scheduled_tasks`, `wallets`.
- The outbox, really: `OpenWalletAction / WalletOpenedEvent / f`, `DepositMoneyAction /
  MoneyDepositedEvent / t`, `RaiseAlertAction / - / - / f`. Three rows, not the page's two, and
  only the subscribed event is delivered.

### Walkthroughs 2-5 (2026-10-17)

The same steps, scripted (`walk.sh` in the session scratchpad), each in an empty folder:

| installer | database | `make test` | served, deposit, alert, tables, outbox |
|---|---|---|---|
| uv | MySQL 8.4 | 11 passed | yes - same three outbox rows (0/1/0) |
| uv | MariaDB 11.4 | 11 passed | yes - same |
| pip + venv | PostgreSQL | 11 passed | - |
| poetry 2.5.1 | PostgreSQL | 11 passed, after `poetry install` | - |

- pip has no scaffold: the reader writes a five-line `pyproject.toml` (setuptools finds a `src/`
  layout by itself) and `pip install -e .`, so the project is importable.
- `poetry add` installs the dependencies but not the project: without `poetry install`, codegen
  fails on `import wallet`.
- MySQL/MariaDB containers: the examples' files default to `root` / `ekbatan` / `wallet`, so the
  `docker run` sets exactly that.

### The page, rebuilt (step 3-4)

- Every file is the example's, imported at build time; `model.py`, `actions.py`, `handlers.py`,
  `jobs.py` and `api.py` are shown once (identical in all three - a test keeps them so), the rest
  per database. `tests/unit/test_setup_guide.py` fails if an example gains a file the page does
  not show.
- Commands per installer: `uv init wallet` / a five-line `pyproject.toml` and `pip install -e .`
  / `poetry new wallet` and `poetry install`; per database: the `docker run`, the image, the
  table check and the outbox query, each with the output really seen.
- `scripts/check-setup-guide.py` reads the page, keeps what the picker shows for one database
  and installer, and runs it in an empty folder; then checks `make test`, the service, the alert,
  the tables and the outbox against the page's own table. **9 of 9 pass.** Planted faults - the
  old `docker compose exec wallet-db`, a wrong outbox value - each fail it.
