# Ledger: a mandatory `validate()` on every model and entity (EKB-0497-)

Asked on 2026-10-23, decided by the user: every concrete `Model` and `Entity` defines
`validate(self) -> None`, the one place for a model's own rules. Ekbatan runs its built-in checks
first and then `validate()`, every time a model is made - `build()`, `copy()...build()`, direct
construction, `evolve` / `without_events`, and `from_record`. A model with no `validate()` is
refused when its class is defined, and a type checker flags it. A model's own `__post_init__`
is made safe: the built-in checks run whether or not it calls `super().__post_init__()`, and
nothing runs twice when it does. Worked alone from this file; judgement calls are in
`docs/DECISIONS-TO-REVIEW.md`, "mandatory validate() (EKB-0497-)".

State: `todo`, `doing`, `done`, `n/a` (says why).

## Library

| # | item | state |
| --- | --- | --- |
| L1 | `validate()` abstract on the base; `ABC` so mypy and pyright both flag a model without it | done - `Persistable(ABC)`, `@abstractmethod validate`; mypy and pyright both flag it (pyright needs `ABCMeta`: measured) |
| L2 | refuse a concrete class without `validate()` at definition; let an explicitly abstract base through | done - `_require_validate` in `__init_subclass__`: `TypeError` with the given message; `ABC` in own bases or other abstract methods left = a base |
| L3 | built-in checks split out, then `validate()`, on every construction path | done - `_check_built_ins()` (Model overrides it), then `validate()`, in `Persistable.__post_init__` |
| L4 | an own `__post_init__` wrapped at definition: built-ins first, user's code, then `validate()`; no double run | done - `_guard_post_init`: built-ins, own code, `validate()`; re-entry kept by `_GUARDED` (ids); `InitVar` args passed through; `slots=True` works |
| L5 | framework's own subclasses, generic aliases, `ModelEvent` not caught | done - `Model`/`Entity` list `ABC`; subscriptions make no class; `ModelEvent`, builders not `Persistable`; no other framework subclass |
| L6 | docstrings in `domain.py` | done - module, `__init_subclass__`, `__post_init__`, `validate`, `_check_built_ins`, `build()` |

## Tests (each red on the old code first)

| # | item | state |
| --- | --- | --- |
| T1 | unit: Model and Entity without `validate()` refused at definition, message checked | done - `tests/unit/test_validate.py`; red on old code |
| T2 | unit: intermediate abstract base allowed; its concrete subclass checked | done - `Audited(Model[ID, S], ABC)` allowed, its subclass refused without `validate`, `Audited(...)` itself refused by Python; red |
| T3 | unit: `validate()` runs on build, copy().build(), direct construction, evolve, without_events | done - constructor, builder, `copy_base` build, fixture `Wallet.copy()...build()`, `evolve`, `without_events`, `next_version`, entity; red |
| T4 | unit: built-in checks run before `validate()` | done - version=0 with a negative balance gives the version error and `validate` never ran; `validate` sees `updated_date` set and a tuple; red |
| T5 | unit: own `__post_init__` without super - version=0 and balance=None refused; with super - nothing twice (counter); several levels | done - `Forgetful` (no super): version=0, balance=None refused; `Careful` / `MoreCareful`: built-ins counted once (patched `_required`); entity and slotted cases; red |
| T6 | mypy and pyright flag a model without `validate()` | done - mypy test always runs; pyright test runs when `pyright` is on PATH (red then green through a `uvx pyright` shim); red: mypy and pyright reported nothing |
| T7 | integration: `validate()` runs on load through a repository, sqlite + postgres, sync and async | done - `tests/integration/test_validate_on_load.py`: get, find_many, find, find_where, find_one_where, all, an entity; 12 red on old `domain.py` (sqlite + postgres, both modes), green after |
| T8 | every test-defined model and entity gets a `validate()` | done - fixtures (`Wallet` balance >= 0, `Notification` recipient), `test_ids`/`test_id_persistence` `Purse`, `Person`, the rest `pass`; source strings in mypy/codegen tests too |

## Examples

| # | item | state |
| --- | --- | --- |
| E1 | every uv example's models and entities get `validate()`; Wallet refuses a negative balance | done - all 14 uv `model.py`: `Wallet` (balance), `Alert` (blank message), `TransferStep` (`pass`) |
| E2 | each example's own test: a negative opening balance is refused | done - `test_a_wallet_never_holds_a_negative_balance` in each of the 14 |
| E3 | `scripts/sync-examples.py` writes pip and Poetry | done - `scripts/sync-examples.py`: 14 x 3 synced |

## Docs

| # | item | state |
| --- | --- | --- |
| D1 | `docs/concepts/models-and-entities.md` + website copies (reference/model, reference/entity, learn/models-and-entities) | done - docs section "`validate()`: the model's own rules"; learn page the same; reference/model section; reference/entity paragraphs, `Currency` uses `validate` |
| D2 | getting started / first model pages, docs and website | done - docs/getting-started, learn/getting-started, learn/your-first-action: `validate` in the model and a paragraph |
| D3 | every other doc or page showing a full model class | done - README, index.astro, docs/wiring + reference/wiring; sharding and class-line fragments left as fragments |
| D4 | AGENTS.md rule | done - AGENTS.md "Every model and entity defines `validate()`" |
| D5 | DRACULAS row 54 + area file A14 marked fixed | done - row 54 struck, fixed (`EKB-0497`); A14 carries the fix note |
| D6 | DIVERGENCE.md | done - new **chosen** section linking DECISIONS; the "must call super" sentence removed |
| D7 | agent-skills | done - maintainer and examples skills, both copies (`agent-skills/`, `.claude/skills/`) |

## Verify

| # | item | state |
| --- | --- | --- |
| V1 | `make lint` | done - `make lint` clean (ruff, mypy --strict over src + 14 examples, links, divergence, builders, docs sync) |
| V2 | unit suite | done - 2063 passed (pyright test included, through a `uvx pyright` shim); 2046 before |
| V3 | integration, four databases, both modes; `make test-psycopg-async`; `make test-gevent` | done - `--dialects=all`: 6579 passed, 0 failed; `make test-psycopg-async`: 1308 passed; `make test-gevent`: 2767 passed |
| V4 | all 14 uv examples `make`; pip and Poetry for two families | done - all 14 uv examples pass (`make`), `wallet-service-uv-sqlite run`; pip + Poetry for saga-pg and wallet-service-sqlite pass. Example test red on the old ekbatan, green on the new |
| V5 | setup-guide checker, uv x 4 databases x 2 modes | done - uv x pg/mysql/mariadb/sqlite x sync/async: 8/8 ok |
| V6 | website build + check; docs-website sync | done - website builds (58 pages), `npm run check` clean; docs-website sync 33 pairs, 0 below 80% |
