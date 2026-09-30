# Website parity

The Java site is 76 files. This counts them, the way `docs/JAVA-PARITY-LEDGER.md` counts the
Java sources: enumerate, read, mark. A feature-driven pass only finds what somebody thought to
look for, and the first pass over this site missed five interactive components and most of the
learn path.

    node scripts/parity.mjs

Status is one of **done** (a counterpart exists and behaves), **partial** (some of it), **todo**,
**n/a** (deliberately not ported, reason given).

## Components

| Java | lines | Python | status |
| --- | --- | --- | --- |
| CodeBlock.astro | 262 | same | done - byte-identical |
| SectionCard.astro | 78 | same | done - byte-identical |
| Nav.astro | 187 | same | done - adapted for content, CSS identical |
| Footer.astro | 100 | same | done - adapted for content, CSS identical |
| LearnPager.astro | 168 | same | done - adapted to this port's page list, CSS identical |
| DatabasePicker.astro | 151 | `DialectPicker.astro` | done - **four dialects here, not three.** Sticky bar, shows and hides `[data-db]` blocks, remembers the choice across pages |
| StackPicker.astro | 302 | `SetupPicker.astro` | done - Java's three axes become two: dialect, and how you install (uv / pip / Poetry). Carries the same per-combination example link |
| GuidedFlow.astro | 424 | same | done - byte-identical; used by the dual-write lesson |
| AliasRenameFlow.astro | 638 | same | done - adapted to this port's spelling. Used by the consuming-events lesson |
| RedirectPage.astro | 26 | same | done - byte-identical |
| NativeStackPicker.astro | 362 | - | n/a - GraalVM. CPython has no counterpart |

## Layouts

| Java | lines | Python | status |
| --- | --- | --- | --- |
| BaseLayout.astro | 55 | same | done |
| DocLayout.astro | 330 | same | done - byte-identical |

## Pages - top level

| Java | lines | Python | status |
| --- | --- | --- | --- |
| index.astro | 342 | same | done - Java's, including the animated diagrams, with this port's content |
| privacy.astro | 114 | same | done - byte-identical |

## Pages - /concepts/

Seven five-line redirect pages, each forwarding a short URL into the matching lesson.

| Java | Python | status |
| --- | --- | --- |
| concepts/index.astro | same | done |
| concepts/actions.astro | same | done |
| concepts/models-and-entities.astro | same | done |
| concepts/outbox.astro | same | done |
| concepts/sagas.astro | same | done |
| concepts/sharding.astro | same | done |
| concepts/the-dual-write-trap.astro | same | done |

## Pages - /learn/

The guided path. Java has thirteen; this port has fifteen, three of them with no Java
counterpart and one of Java's with none here.

**A file existing is not parity.** The first pass over this table marked every row *done*
because a file of the same name existed, and the rendered pages are roughly half the length of
Java's: the material is mostly there, in `docs/`, but the lessons were written thin. The
`sections` columns below count `##` and `###` headings in each source, which is the cheapest
honest proxy, and the status is about the *content* rather than the file.

| Java | lines | §§ | Python | lines | §§ | status |
| --- | --- | --- | --- | --- | --- | --- |
| learn/index.astro | 124 | - | same | - | - | done |
| learn/getting-started.mdx | 213 | 8 | same | 142 | 9 | done - 7 steps, the closing tour and next steps, as Java's |
| learn/the-dual-write-trap.mdx | 63 | 4 | same | 72 | 5 | done - uses GuidedFlow for both diagrams |
| learn/outbox.mdx | 107 | 4 | same | 182 | 5 | done - the DDL switches with the dialect picker |
| learn/models-and-entities.mdx | 254 | 9 | same | 203 | 9 | done - soft deletion, the inherited builder table and naming conventions added |
| learn/actions.mdx | 191 | 9 | same | 170 | 7 | done - Java's 9 sections in 7: singletons folds in thread-forking, two refusals folds in nesting and cross-shard |
| learn/your-first-action.mdx | 236 | 9 | same | 263 | 11 | done - event first, then model, repository, action, endpoint, curl, and the outbox read back |
| learn/consuming-events.mdx | 319 | 17 | same | 345 | 18 | done - four topologies, then both paths end to end |
| learn/sharding.mdx | 242 | 9 | same | 203 | 12 | done - six steps including the cross-shard transfer |
| learn/sharding-strategies.mdx | 100 | 8 | same | 116 | 8 | done - a subsection per strategy |
| learn/sagas.mdx | 181 | 7 | same | 134 | 8 | done - design rules added |
| learn/complete-project-setup.mdx | 3085 | 20 | same | 661 | 16 | done - Java's 20 sections in 16: its 5a-5d are Flyway file variants, and so are these - `V0001`-`V0003` SQL files |
| learn/native-image.mdx | 338 | - | - | - | - | n/a - GraalVM |
| - | - | - | `learn/install.mdx` | 94 | 4 | done - no Java counterpart at this size; Java's install lives inside complete-project-setup |
| - | - | - | `learn/wiring-a-service.mdx` | 140 | 5 | done |
| - | - | - | `learn/going-to-production.mdx` | 112 | 5 | done - no Java counterpart. Threads, the GIL and pool sizing have nowhere else to go |

## Pages - /reference/

Java hand-writes its reference as MDX, one page per class or subsystem. **So does this now.**
The section used to be `docs/` rendered through one dynamic route, which meant the website could
not have its own structure - the docs tree decided the URLs, the grouping and the page
boundaries, and the result did not look like Java's. The website now owns these pages;
`docs/` stays what it is, the repository's own documentation for somebody reading the
repository.

Same groups, same order, same URLs, except where the thing itself differs.

| Java | Python | status |
| --- | --- | --- |
| reference/index.astro | same | done - hand-written registry, Java's five groups plus Dialects |
| reference/action.mdx | same | done |
| reference/action-plan.mdx | same | done |
| reference/action-executor.mdx | same | done |
| reference/model.mdx | same | done |
| reference/entity.mdx | same | done |
| reference/model-event.mdx | same | done |
| reference/repository.mdx | same | done |
| reference/transaction-manager.mdx | same | done |
| reference/sharding.mdx | same | done |
| reference/keyed-locks.mdx | same | done - five backends here, four there |
| reference/multi-database.mdx | same | done |
| reference/tables/index.mdx | same | done |
| reference/tables/events.mdx | same | done |
| reference/tables/event-notifications.mdx | same | done |
| reference/tables/scheduled-tasks.mdx | same | done |
| reference/runtime/observability.mdx | same | done |
| reference/jobs/distributed-jobs.mdx | same | done |
| reference/events/local-event-handler.mdx | same | done |
| reference/events/streaming.mdx | same | done |
| reference/di/without-di.mdx | `reference/wiring.mdx` | done - the same page, under the name that fits a language with no DI container |
| reference/di/spring-boot.mdx, di/quarkus.mdx, di/micronaut.mdx | - | n/a - no DI containers to integrate with |
| reference/annotations.mdx | - | n/a - `@EkbatanAction` and friends. No annotation processor here |
| reference/runtime/native-image.mdx | - | n/a - GraalVM |
| reference/outbox-schema.mdx | - | n/a - a redirect stub for an old Java URL that never existed here |
| - | `reference/wiring-fastapi.mdx` | done - no Java counterpart; the HTTP seam is where two sharp mistakes live |
| - | `reference/migrations.mdx` | done - no Java counterpart as a page; Java documents Flyway inside other pages |
| - | `reference/schemagen.mdx` | done - no Java counterpart as a page; Java's jOOQ step lives in its build |
| - | `reference/runtime/concurrency.mdx` | done - no Java counterpart. Virtual threads settle it there |
| - | `reference/dialects/{postgresql,mysql,mariadb,sqlite}.mdx` | done - four pages where Java keeps dialect specifics inside multi-database |

## Assets and config

| Java | Python | status |
| --- | --- | --- |
| public/fonts (11) | same | done - byte-identical |
| styles/fonts.css, styles/styles.css | same | done - byte-identical |
| plugins/remark-base-path.mjs | same | done - byte-identical |
| astro.config.mjs, package.json, tsconfig.json | same | done - Astro 7 rather than 6 |
| - | `styles/palette.css` | done - no Java counterpart. The one colour knob |
| - | `plugins/remark-doc-links.mjs` | done - no Java counterpart. Rewrites the docs' repo-relative links |
| - | `plugins/remark-strip-lead.mjs` | done - no Java counterpart |
| - | `scripts/check-classes.mjs` | done - no Java counterpart |
