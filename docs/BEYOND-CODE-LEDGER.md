# Parity beyond the code

`JAVA-PARITY-LEDGER.md` counts the Java **source** files and is finished: 246 of 247 settled.
This counts everything else - the documentation, the website, the release machinery, the files a
repository needs before anyone can use what is in it.

It exists for the same reason the first one does. A feature-driven pass only finds what the
porter thought to look for; enumerating the files makes completeness countable rather than
hopeful. The first ledger found that three rows had been marked done by belief rather than by
reading, and one of them was hiding a real gap. This one starts from the filesystem.

    python scripts/ledger.py docs/BEYOND-CODE-LEDGER.md

Status is one of:

- **done** - there is a Python counterpart and it says the right thing for this port
- **partial** - some of it exists; what is missing is named
- **todo** - not written. A Python file may exist and be thin; that is not the same thing
- **n/a** - deliberately not ported; the reason is given

## What is not counted

| what | files | why |
| --- | --- | --- |
| `build-logic/`, `gradle/`, `plugins/`, `gradlew*`, `settings.gradle.kts` | 30 | Gradle machinery. `pyproject.toml` and the Makefiles are the counterpart and they exist |
| `config/` | 1 | Spotless configuration. Ruff's settings live in `pyproject.toml` |
| `website/dist`, `website/node_modules` | many | build output |

## The decision this ledger cannot make for you

**The website.** Java's is 76 source files of Astro at `ekbatan-io.github.io/ekbatan`, and this
port's README already links to it. Three options, and they are very different amounts of work:

1. **Add Python pages to the existing site** - one site, a language switcher, shared concept
   pages. Least work, and a reader comparing the two ports never leaves the page.
2. **A second site** for `ekbatan-py`. Most work, and the concept pages would be maintained twice.
3. **No site**; `docs/` and the README carry it.

Every row under *Website* below is `todo` pending that choice, and the count reflects it. Pick
one and the rows either collapse to `n/a` or become real work.

---

## Repository essentials

The smallest set, and the one with a real defect in it.

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| LICENSE | 201 | `LICENSE` | done - copied byte for byte. Verified in both distributions: the wheel carries `dist-info/licenses/LICENSE` and the sdist carries `LICENSE`. **`db-scheduler/` had the same defect and it was fixed with it** |
| README.md | 512 | `README.md` | done - gained *Install*, *Versioning and support*, the full example list and a link to RELEASE.md. Four stale claims corrected on the way: it named yoyo rather than Alembic, and its test counts (175/56/118) were collected as 578/250/322 |
| AGENTS.md | 402 | `AGENTS.md` | done - Java's headings, with what this port learned in them: the traps the suite fell into, the ledger method, the ContextVar rule, and "do not commit in the Java repository" |
| CLAUDE.md | 3 | `CLAUDE.md` | done - a pointer to AGENTS.md, as Java's is |
| RELEASE.md | 120 | `RELEASE.md` | done - PyPI trusted publishing: no GPG, no Sonatype, no stored secret. Two distributions in dependency order, the TestPyPI rehearsal, and the warning Java has no counterpart to - **a PyPI filename is burned forever, deleting a release does not free the version** |

## Continuous integration and release

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| .github/workflows/ci.yml | 180 | `.github/workflows/ci.yml` | done - lint, unit, four dialects, examples, tracing. **Has never run: this repository has no remote** |
| .github/workflows/heavy-verification.yml | 96 | - | n/a - native-image and the example grid build. Neither has a Python counterpart |
| .github/workflows/release.yml | 110 | `.github/workflows/release.yml` | done - verify, build, smoke, publish, release. `uv publish --trusted-publishing always`, so no secret exists to leak. Written with RELEASE.md rather than an iteration later, because a procedure describing a workflow that does not exist is the thing this ledger exists to prevent |
| .github/workflows/deploy-website.yml | 40 | - | todo - blocked on the website decision above |
| - | - | `.github/workflows/db-scheduler.yml` | done - no Java counterpart; the vendored distribution has its own suite |

## Documentation - the concepts

Language-neutral ideas with Java samples. The prose largely transfers; every code block has to
be rewritten, and some of the reasoning changes with it - `sagas.md` has to explain a 202, and
`sharding.md` has to explain that ids carry their shard here exactly as they do there.

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| concepts/README.md | 13 | `docs/concepts/README.md` | done |
| concepts/the-dual-write-trap.md | 55 | `docs/concepts/the-dual-write-trap.md` | done - almost entirely prose, and it transferred almost entirely. Gained one section: the cross-shard action is the one place the trap reappears |
| concepts/outbox.md | 66 | `docs/concepts/outbox.md` | done - carries the column list as a cross-language contract, including why `modelId`/`modelName` are spelled in Java's style, and the frozen-clock ordering trap |
| concepts/actions.md | 184 | `docs/concepts/actions.md` | done - `ContextVar` in place of `ScopedValue`. Names the retry divergence: matching here is by `isinstance`, so a subclass matches, where Java's is exact by class |
| concepts/models-and-entities.md | 247 | `docs/concepts/models-and-entities.md` | done - the `@AutoBuilder` section shrinks to the one thing Python cannot infer, and records the place this port is **ahead**: no `@JsonCreator` boilerplate, because `decode` is driven by the dataclass's own annotations |
| concepts/sagas.md | 175 | `docs/concepts/sagas.md` | done - gained the section Java has no counterpart to: **202, not 200**, and why idempotency is a unique constraint in the same transaction rather than a check-then-act |
| concepts/sharding.md | 92 | `docs/concepts/sharding.md` | done - same two axes and the same bit layout, which is a cross-language contract. Gained a section saying fan-out is sequential here |

## Documentation - the database

The largest area, and the most portable. Everything here was checked by **generating the DDL and
comparing it against Java's**, not by translating Java's prose - which is how the findings below
were found. The four forward references stripped when getting-started and wiring were written are
restored; `scripts/links.py`, now part of `make lint`, is what confirmed it. what the tables are, how repositories read, how locks
and transactions behave. The per-dialect pages need real checking rather than translation - this
port's MySQL and MariaDB behaviour is not identical to Java's.

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| database/README.md | 21 | `docs/database/README.md` | done - the index, plus the three things that must line up to interoperate with a Java service |
| database/tables.md | 42 | `docs/database/tables.md` | done - the map. These are `Table` objects built by a factory rather than DDL you copy, so the page says how to build them |
| database/tables/events.md | 153 | `docs/database/tables/events.md` | done - **they do agree, column for column, verified by generating both.** Two differences found: MariaDB's native UUID (checked to interoperate anyway) and PostgreSQL's missing partial index, **measured at 3,528 kB against Java's 56 kB** |
| database/tables/event-notifications.md | 171 | `docs/database/tables/event-notifications.md` | done - identical columns and unique constraint. Same partial-index gap on PostgreSQL |
| database/tables/scheduled-tasks.md | 124 | `docs/database/tables/scheduled-tasks.md` | done - Java uses `timestamptz` here and this port `timestamp`. **Verified safe**: the scheduler's clock is aware, and an aware datetime stores correctly either way. Carries the measurement showing a *naive* write shifting four hours under a non-UTC session |
| database/outbox-schema.md | 11 | - | n/a - a redirect stub preserving an old Java URL. This port has no old URLs |
| database/repositories.md | 213 | `docs/database/repositories.md` | done - and a good half of the Java page had no counterpart, as expected: mapping by field name deletes `fromRecord`/`toRecord` entirely |
| database/transaction-manager.md | 174 | `docs/database/transaction-manager.md` | done - one `with` block against four overloads, and a sharper divergence than expected: **this port joins an open transaction where Java nests onto a second connection**, which Java documents as "do not nest" |
| database/keyed-locks.md | 248 | `docs/database/keyed-locks.md` | done - carries the `GET_LOCK` table (MySQL truncates a sub-second wait to zero; MariaDB honours it) and the watchdog's thread cost |
| database/sharding.md | 323 | `docs/database/sharding.md` | done - the reference half; the reasoning is in `concepts/sharding.md`. Documents why `max_overflow` defaults to 0 where SQLAlchemy's default is 10 |
| database/multi-database.md | 136 | `docs/database/multi-database.md` | done - Java's grid of types against converters collapses to three column helpers, because a SQLAlchemy type decorator renders itself per dialect |
| database/postgresql.md | 205 | `docs/database/postgresql.md` | done |
| database/mysql.md | 267 | `docs/database/mysql.md` | done |
| database/mariadb.md | 261 | `docs/database/mariadb.md` | done - leads with the scheme trap, because a url left as `mysql://` tests the wrong code path silently |
| database/jooq-codegen.md | 315 | `docs/database/schemagen.md` | done - and the type-forcing section is indeed the part that matters |
| - | - | `docs/database/migrations.md` | done - **no Java counterpart as a page**; Java documents Flyway inside getting-started and wiring. Needed here because Alembic is driven as a library, and because the checksum guard had to be added on top of it |
| - | - | `docs/database/sqlite.md` | done - **no Java counterpart.** Says plainly why it is supported and why it is not a production dialect: no `SKIP LOCKED`, and advisory types that let a mapping bug pass |

## Documentation - events and jobs

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| events/README.md | 19 | `docs/events/README.md` | done - the four topologies, and that every one of them is at-least-once |
| events/local-event-handler.md | 364 | `docs/events/local-event-handler.md` | done - and it says so, with the consequence spelled out: **handlers on one event do not run concurrently here and do in Java**, so a handler written against this port's ordering will not survive the other one. Every tuning default matches Java's; the ISO-8601 duration trap does not exist here |
| events/event-streaming.md | 365 | `docs/events/event-streaming.md` | done - and that is exactly what it says. What this port owes the pipeline is the row format, and `ActionEvent` is the consumer-side name for it |
| jobs/README.md | 7 | `docs/jobs/README.md` | done |
| jobs/distributed-jobs.md | 237 | `docs/jobs/distributed-jobs.md` | done - both carried. Also records one thing Java must do that this port does not: Java pins the SQL dialect by hand because db-scheduler detects it by opening a connection and can lose that race; SQLAlchemy resolves it from the url with no I/O |

## Documentation - getting started and wiring

Where the two ports diverge most, and where the page count collapses. Java documents two build
tools and three DI frameworks; this port has one of each and no DI at all.

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| gradle/getting-started.md | 368 | `docs/getting-started.md` | done - one page replaces four, and most of Java's content evaporates: no BOM to override, no annotation processor on two classpaths, no `-parameters`, no compile-time visitor. What replaces it is the Makefile, which is the one thing uv has no answer for |
| maven/getting-started.md | 712 | (the same page) | n/a - collapsed into the one above |
| gradle/README.md, maven/README.md | 55 | - | n/a - build-tool index pages |
| gradle/jooq-codegen.md | 387 | `docs/database/schemagen.md` | n/a - collapsed into the schemagen page |
| maven/jooq-codegen.md | 521 | (the same page) | n/a - as above |
| wiring/without-di.md | 323 | `docs/wiring.md` | done - eight blocks, each removable on its own, taken from `fastapi_wallet_rest_pg/app.py` so every line of it runs. Carries the three-pool argument and says plainly what registering by hand costs against `@EkbatanAction` |
| wiring/annotations.md | 236 | - | n/a - `@EkbatanAction` and friends; there is no annotation processor here |
| wiring/spring.md | 439 | - | n/a - no Python counterpart |
| wiring/quarkus.md | 443 | - | n/a |
| wiring/micronaut.md | 474 | - | n/a |
| wiring/README.md | 16 | - | n/a |
| - | - | `docs/wiring-fastapi.md` | done - **no Java counterpart.** The lifespan, the module-level `Annotated` alias (a 422 on every endpoint if nested), plain `def` over `async def`, exception handlers that must return rather than raise, and a lease that must span the commit |

## Documentation - runtime

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| runtime/README.md | 8 | `docs/runtime/README.md` | done |
| runtime/observability.md | 126 | `docs/runtime/observability.md` | done - **every span name, attribute and metric verified identical to Java's.** Two differences, both deliberate: the instrumentation scope names this library rather than Java's, and three extra attributes report a cross-shard partial commit on the span where Java logs it at CRITICAL |
| runtime/native-image.md | 172 | - | n/a - GraalVM. CPython has no counterpart |
| - | - | `docs/runtime/concurrency.md` | done - **no Java counterpart, and it was the most needed page in the tree.** Java settles this in two words; here it is five separate decisions, the measured asyncio cost (5 heartbeat ticks against ~55), pool sizing per process, and where the GIL actually bites |
| docs/README.md | 14 | `docs/README.md` | done - the index |
| medium/01-event-driven-persistence.md | 211 | - | n/a - an article, not documentation |

## Website

Every row here is blocked on the decision at the top of this file.

| Java | files | Python | status |
| --- | --- | --- | --- |
| website/src/pages/concepts | 7 | - | todo |
| website/src/pages/learn | 9 | - | todo |
| website/src/components, layouts | 13 | - | todo |
| website/src/pages (index, other) | 4 | - | todo |
| website/public/fonts | 11 | - | todo |
| website/astro.config.mjs, package.json, tsconfig.json | 3 | - | todo |

## Process

Neither is user-facing, and both are how the Java repository is actually worked on.

| Java | files | Python | status |
| --- | --- | --- | --- |
| openspec/ | 54 | - | todo - the change-proposal workflow. `openspec/changes/fix-defects-found-porting-to-python` is where this port's Java findings were filed, so the two repositories already share it |
| agent-skills/ | 12 | - | todo - skills for agents working in the repository |
