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
| concepts/README.md | 13 | `docs/concepts/README.md` | todo |
| concepts/the-dual-write-trap.md | 55 | `docs/concepts/the-dual-write-trap.md` | todo - the argument for the whole framework; almost entirely prose |
| concepts/outbox.md | 66 | `docs/concepts/outbox.md` | todo |
| concepts/actions.md | 184 | `docs/concepts/actions.md` | todo |
| concepts/models-and-entities.md | 247 | `docs/concepts/models-and-entities.md` | todo |
| concepts/sagas.md | 175 | `docs/concepts/sagas.md` | todo - `ekbatan-py-examples/fastapi_wallet_saga_pg` is the worked example |
| concepts/sharding.md | 92 | `docs/concepts/sharding.md` | todo |

## Documentation - the database

The largest area, and the most portable: what the tables are, how repositories read, how locks
and transactions behave. The per-dialect pages need real checking rather than translation - this
port's MySQL and MariaDB behaviour is not identical to Java's.

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| database/README.md | 21 | `docs/database/README.md` | todo |
| database/tables.md | 42 | `docs/database/tables.md` | todo |
| database/tables/events.md | 153 | `docs/database/tables/events.md` | todo - a cross-language contract; the two must agree column for column |
| database/tables/event-notifications.md | 171 | `docs/database/tables/event-notifications.md` | todo |
| database/tables/scheduled-tasks.md | 124 | `docs/database/tables/scheduled-tasks.md` | todo |
| database/outbox-schema.md | 11 | `docs/database/outbox-schema.md` | todo |
| database/repositories.md | 213 | `docs/database/repositories.md` | todo - mapping is by field name here, so a good half of the Java page has no counterpart |
| database/transaction-manager.md | 174 | `docs/database/transaction-manager.md` | todo - `with` blocks rather than four callback overloads |
| database/keyed-locks.md | 248 | `docs/database/keyed-locks.md` | todo - must carry the measured `GET_LOCK` sub-second finding |
| database/sharding.md | 323 | `docs/database/sharding.md` | todo |
| database/multi-database.md | 136 | `docs/database/multi-database.md` | todo |
| database/postgresql.md | 205 | `docs/database/postgresql.md` | todo |
| database/mysql.md | 267 | `docs/database/mysql.md` | todo |
| database/mariadb.md | 261 | `docs/database/mariadb.md` | todo |
| database/jooq-codegen.md | 315 | `docs/database/schemagen.md` | todo - the counterpart is `ekbatan.schemagen`, and the type-forcing section is the part that matters |
| - | - | `docs/database/sqlite.md` | todo - **no Java counterpart.** SQLite is a supported dialect here and is not one there |

## Documentation - events and jobs

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| events/README.md | 19 | `docs/events/README.md` | todo |
| events/local-event-handler.md | 364 | `docs/events/local-event-handler.md` | todo - must say that fan-out and dispatch are sequential here |
| events/event-streaming.md | 365 | `docs/events/event-streaming.md` | todo - the Debezium connector is shared; what differs is only that no Python artifact is involved |
| jobs/README.md | 7 | `docs/jobs/README.md` | todo |
| jobs/distributed-jobs.md | 237 | `docs/jobs/distributed-jobs.md` | todo - must carry `workers(n)` and the missing shutdown hook |

## Documentation - getting started and wiring

Where the two ports diverge most, and where the page count collapses. Java documents two build
tools and three DI frameworks; this port has one of each and no DI at all.

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| gradle/getting-started.md | 368 | `docs/getting-started.md` | todo - one page replaces four. `uv add ekbatan`, the extras, the first action |
| maven/getting-started.md | 712 | (the same page) | n/a - collapsed into the one above |
| gradle/README.md, maven/README.md | 55 | - | n/a - build-tool index pages |
| gradle/jooq-codegen.md | 387 | `docs/database/schemagen.md` | n/a - collapsed into the schemagen page |
| maven/jooq-codegen.md | 521 | (the same page) | n/a - as above |
| wiring/without-di.md | 323 | `docs/wiring.md` | todo - **this is the Python story**, and the only wiring page that transfers |
| wiring/annotations.md | 236 | - | n/a - `@EkbatanAction` and friends; there is no annotation processor here |
| wiring/spring.md | 439 | - | n/a - no Python counterpart |
| wiring/quarkus.md | 443 | - | n/a |
| wiring/micronaut.md | 474 | - | n/a |
| wiring/README.md | 16 | - | n/a |
| - | - | `docs/wiring-fastapi.md` | todo - **no Java counterpart**, and the nearest thing to its DI pages: a lifespan, a dependency, three pools |

## Documentation - runtime

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| runtime/README.md | 8 | `docs/runtime/README.md` | todo |
| runtime/observability.md | 126 | `docs/runtime/observability.md` | todo - the span and metric names are a cross-language contract |
| runtime/native-image.md | 172 | - | n/a - GraalVM. CPython has no counterpart |
| - | - | `docs/runtime/concurrency.md` | todo - **no Java counterpart, and needed.** Java's answer is "virtual threads"; this port needs a page on threads, the GIL, asyncio and what blocks |
| docs/README.md | 14 | `docs/README.md` | todo - the index |
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
