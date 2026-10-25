# Event handlers extend a base class with Java's methods - ledger (Dracula 71)

The brief (the user, 2026-10-25): "exactly match java". Java's handler is an interface a class
implements, with four methods:

```java
public interface EventHandler<E extends ModelEvent<?>> {
    String name();
    default Set<String> aliases() { return Set.of(); }
    Class<E> eventType();
    void handle(EventEnvelope<E> envelope) throws Exception;
}
```

The Python handler was a `Protocol`: any object with `name` and `event_type` attributes and a
`handle` method, and `aliases` read with `getattr(..., ())`. Now `EventHandler` and
`AsyncEventHandler` are abstract base classes a handler extends, with `name()`, `aliases()`,
`event_type()` and `handle()` - Java's four, in Java's order - and the registry, fan-out and
dispatch call them.

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "Event handlers extend a base class with
Java's methods (EKB-0548-)". Status: `todo` / `doing` / `done` / `skipped` (by the user). A row is `done` only when a run
shows it.

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | `EventHandler[E]` / `AsyncEventHandler[E]`: ABCs, abstract `name()`, `aliases()` empty by default, abstract `event_type()`, abstract `handle()` (`async def` on the async one) | done | `src/ekbatan/events/handler.py`: a private `_HandlerCore[E]` (ABC) holds `name()`, `aliases()` returning `frozenset()`, `event_type()`; `EventHandler` adds an abstract `handle()`, `AsyncEventHandler` an abstract `async def handle()`. mypy clean (224 files) |
| F2 | the registry calls the methods; a handler extending neither base is refused with a `TypeError`; the checks keep Java's order, their messages in the method form | done | `add` calls the methods; `_refuse_a_handler_of_no_kind` raises `TypeError` for neither base, or a `handle()` of the other kind; the messages in the method form - `handler.name() cannot be blank`, `handler.event_type() cannot be None`, `handler.event_type().__name__ cannot be blank`, `handler.aliases() cannot be None`, `handler.aliases() cannot contain blank values` - in Java's order |
| F3 | fan-out and dispatch call the methods; dispatch tells an async handler by its base class | done | fan-out writes `handler.name()`; dispatch builds the envelope from `handler.event_type()` and runs an `AsyncEventHandler` on the handler loop, told by its base; a sync handler whose `handle()` hands back an awaitable anyway is a failed attempt with a `TypeError` |
| F4 | docstrings in `src/` | done | the module and class docstrings; no other `src/` docstring described handler attributes |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | new: a handler extending neither base is refused; `aliases()` defaults to empty and an override is honoured; a sync and an async handler run through dispatch in the method form - red on the old code, then green | done | 4 new tests - `test_aliases_default_to_empty_and_an_override_is_honoured`, `test_a_handler_that_extends_neither_base_is_refused`, `test_a_handle_of_the_other_kind_is_refused` (unit), `test_a_sync_and_an_async_handler_in_the_method_form_both_run_through_dispatch` (integration): 4 failed on the old code, 4 passed on the new |
| T2 | every test handler and fixture in the method form | done | `tests/fixtures/handlers.py` (`RecordingHandler` takes `aliases=`, `AsyncTwin` delegates the methods), `test_event_handler_registry.py`, `test_errors.py`, `test_events.py`, `test_async_handlers.py`; the whole SQLite suite 3280 passed, 109 skipped, 0 failed |

## Examples and the guide

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | the 14 uv examples' handlers; `sync-examples.py` writes the pip and Poetry copies, `--check` | done | 16 handler classes in the 14 uv examples converted (`name()` and `event_type()` after the constructor, as Java's are); `sync-examples.py --check` in step; ruff and mypy clean; the 14 uv examples pass (`make -C`, each its wheel installed: REST 6 tests each, saga 6, job worker 4, SQLite 4) and both SQLite `make run`s exit 0. The pip and Poetry copies have the uv `src/` byte for byte (`--check`) and were not run |
| E2 | the setup guide's project (`website/src/setup-guide/`) | done | `website/src/setup-guide/{sync,async}/handlers.py`; the page's handler step names the base; the setup-guide check passes 8 of 8 (PostgreSQL, MySQL, MariaDB, SQLite; uv; sync and async) |

## Probes and docs

| # | item | status | proof |
| --- | --- | --- | --- |
| P1 | parity-probe scripts | done | 9 probe scripts: class-attribute handlers, `Rec` (properties) and `H` (instance attributes) in the method form, their reads of `h.name` now `h.name()`; all 9 compile (`py_compile`); recordings not re-recorded |
| D1 | docs/ pages | done | `events/local-event-handler.md`, `database/tables/event-notifications.md`, `concepts/sagas.md`, `going-async.md`, `runtime/async.md`, `wiring-fastapi.md`, `getting-started.md`: snippets in the method form, prose `name()` / `aliases()`, the definition paragraph rewritten |
| D2 | website pages, in step with docs/ | done | the website twins, `learn/consuming-events.mdx`, the setup guide's handler step; links, check-api (546 snippets, 0 wrong), docs-website-sync (34 pairs), `npm run build`, `npm run check` pass |
| D3 | `AGENTS.md`, `agent-skills/` and its mirrors, `DIVERGENCE.md` | done | `AGENTS.md` (the event-handler rule); `DIVERGENCE.md` ("A handler extends one of two bases", chosen; `divergence.py` passes); `agent-skills/` describes no handler shape, so it and its mirrors stay as they are |
| D4 | `DRACULAS.md` #71 (and its B detail) matched; #96 no longer overstated for handlers | done | #71 matched in the list and its B detail; #96 notes that handlers answer methods |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| R1 | unit, `--dialects=sqlite` | done | the whole default run (unit and every test on SQLite): 3280 passed, 109 skipped, 0 failed; after the guide and example edits, `test_setup_guide.py` and `test_structure.py` again green |
| R2 | `--dialects=postgres,mysql,mariadb`, on its own | skipped | skipped by the user (2026-10-25): stopped part way, at 45% with no failure so far, and not re-run |
| R3 | `make test-psycopg-async`, `make test-gevent` | skipped | skipped by the user (2026-10-25): not run |
| R4 | ruff, ruff format, mypy, links, divergence, builders, sync, check-api, docs-website-sync, `npm run build`, `npm run check` | done | on the final tree: ruff and ruff format over `make lint`'s scope (735 files), mypy (224 files), links, divergence, builders (15), check-api (546 snippets, 0 wrong), docs-website-sync (34 pairs), `npm run build` (59 pages), `npm run check` - all pass |
| R5 | the 14 uv examples, both SQLite `make run`s | done | 14 of 14 uv examples pass, and both SQLite `make run`s exit 0 |
| R6 | the setup-guide check, every database, uv, sync and async | done | 8 of 8 ok - PostgreSQL, MySQL, MariaDB and SQLite, uv, sync and async; ports 5432/3306/8000 free first |

## Commits

| # | item | status | proof |
| --- | --- | --- | --- |
| C1 | EKB-0548 framework and tests; EKB-0549 examples and the guide; EKB-0550 docs, website, AGENTS.md, skills, Draculas, ledger, decisions | done | EKB-0548 framework, tests and probes; EKB-0549 the examples and the guide's project; EKB-0550 docs, website, AGENTS.md, DIVERGENCE.md, Draculas, this ledger, decisions - on main, not pushed |
