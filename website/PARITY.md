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

The guided path. Java has thirteen; this port has five, and three of Java's have no counterpart.

| Java | lines | Python | status |
| --- | --- | --- | --- |
| learn/index.astro | 124 | same | done |
| learn/getting-started.mdx | 213 | same | done - one wallet change, end to end |
| learn/the-dual-write-trap.mdx | 63 | same | done - uses GuidedFlow for both diagrams |
| learn/outbox.mdx | 107 | same | done - the DDL switches with the dialect picker |
| learn/models-and-entities.mdx | 254 | same | done - carries the frozen-dataclass reasoning |
| learn/actions.mdx | 191 | same | done - and names the isinstance-vs-exact retry divergence |
| learn/your-first-action.mdx | 236 | same | done |
| learn/consuming-events.mdx | 319 | same | done |
| learn/sharding.mdx | 242 | same | done - one database to two, in five steps |
| learn/sharding-strategies.mdx | 100 | same | done |
| learn/sagas.mdx | 181 | same | done - carries the 202 |
| learn/complete-project-setup.mdx | 3085 | same | done - ten sections, every one driven by the picker |
| learn/native-image.mdx | 338 | - | n/a - GraalVM |
| - | - | `learn/install.mdx` | done - no Java counterpart at this size; Java's install lives inside complete-project-setup |
| - | - | `learn/wiring-a-service.mdx` | done |
| - | - | `learn/going-to-production.mdx` | done - no Java counterpart. Threads, the GIL and pool sizing have nowhere else to go |

## Pages - /reference/

Java hand-writes 24 MDX pages. This port renders its whole `docs/` tree - 35 pages - through one
dynamic route, so the *coverage* is larger and the *curation* is not: Java has a page per API
type where this has a page per document.

| Java | Python | status |
| --- | --- | --- |
| reference/index.astro | same | done - built from the collection |
| reference/{action,action-plan,action-executor}.mdx | `/reference/concepts/actions/` | partial - one page where Java has three |
| reference/{model,entity,model-event}.mdx | `/reference/concepts/models-and-entities/` | partial - one page where Java has three |
| reference/repository.mdx | `/reference/database/repositories/` | done |
| reference/transaction-manager.mdx | `/reference/database/transaction-manager/` | done |
| reference/keyed-locks.mdx | `/reference/database/keyed-locks/` | done |
| reference/sharding.mdx | `/reference/database/sharding/` | done |
| reference/multi-database.mdx | `/reference/database/multi-database/` | done |
| reference/tables/*.mdx (4) | `/reference/database/tables/*` | done |
| reference/events/*.mdx (2) | `/reference/events/*` | done |
| reference/jobs/distributed-jobs.mdx | `/reference/jobs/distributed-jobs/` | done |
| reference/runtime/observability.mdx | `/reference/runtime/observability/` | done |
| reference/outbox-schema.mdx | - | n/a - a redirect stub for an old Java URL |
| reference/annotations.mdx | - | n/a - `@EkbatanAction` and friends. No annotation processor here |
| reference/di/*.mdx (4) | `/reference/wiring/`, `/reference/wiring-fastapi/` | partial - Java's without-di transfers; the three DI pages do not |
| reference/runtime/native-image.mdx | - | n/a - GraalVM |

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
