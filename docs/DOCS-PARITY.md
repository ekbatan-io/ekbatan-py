# Docs parity

`website/CONTENT-PARITY.md` counts whether the **site** says what the Java site says. This counts
the same for the **Markdown tree**, and it needs no build: the Java docs sit next door and both
sides are plain files.

    python scripts/docs-parity.py              # per page
    python scripts/docs-parity.py --sections   # per section, within the pages

Two numbers, both proxies:

| | |
| --- | --- |
| **§ match** | the share of Java's headings with a near-match here - structural drift |
| **words** | ours as a share of Java's - volume drift |

Every page and every section ends in one of four states, and two of them are held in the script
so they cannot rot into prose nobody rechecks:

| | |
| --- | --- |
| **done** | >=90% of headings, >=75% of the words |
| **capped** | the remaining gap is accounted for; `CAPPED` / `SECTIONS_OK` hold the reason |
| **n/a** | Java has it and this port should not; `NA` holds the reason |
| **partial** / **todo** | content is still owed |

## Where the drift came from

**The tree was written first and compared afterwards.** Almost every page had the content and
called it something else: `Where to take it` for `Where to acquire the lock`, `Getting one` for
`What it owns`, `Three helpers instead of a converter table` for `Column-type cheatsheet`. A
reader moving between the two ports could not tell they were reading the same section, and the
count read the difference as missing content. **48 headings** were renamed to the ones Java uses
where the body was already the same thing.

That is not cosmetic: six of the renames invalidated anchors that other pages linked to, which
is how you find out the rename mattered.

**The dialect pages were a third of Java's.** `postgresql`, `mysql` and `mariadb` were 32-35%,
missing `Migration order` (including the `*_init.sql` grant idiom, which is real operational
knowledge and not Flyway-specific), `Framework tables` and `Domain tables`. Written for all four
dialects, in `docs/` and on the website at once.

**And the tree had fallen behind the website.** The same one-directional drift
`docs-website-sync.py` was written to catch, except that check compared **word counts per page**
and these gaps were **sections**: 45 of them, including `Sharded repositories` (466 words,
absent), `How rows get there` (654 vs 222) and `Defining a handler` (507 vs 279). The check has a
heading dimension now, and eleven more pairs - the dialect pages, the concepts pages, the tables
index - that it was not looking at at all.

## What is capped, and why

- **`database/{postgresql,mysql,mariadb}.md`** - Java inlines the full DDL of all three framework
  tables on each of its three dialect pages. That is the same 400 lines three times. Here each
  page says what those tables look like on that dialect and links to the one place the DDL lives.
- **`database/jooq-codegen.md`** - jOOQ generates code from a schema; `schemagen` reflects a
  migrated database into `Table` objects. Same job, almost no shared surface to compare.
- **`wiring/without-di.md`** - `The full wiring` and `What each block does` are one numbered
  walkthrough here, and `Same thing with less code` says to use a DI container, which is the
  thing this page exists because there is not one.
- **`concepts/actions.md`** - the per-call state is a `ContextVar`, not a `ScopedValue`.
- **`concepts/models-and-entities.md`** - no annotation processor, so no `@AutoBuilder`.
- **`database/transaction-manager.md`** - named `tm.transaction()`: one `with` block where Java
  needs four overloads.
- **`database/tables/{event-notifications,scheduled-tasks}.md`** - one `MySQL and MariaDB` block,
  because the generated DDL is identical on both.

## What the parity numbers cannot see

Same as the website: both views go green while the tree is still wrong in ways neither counts.
What that pass turned up here, and what checks it now:

| Found | Now checked by |
|---|---|
| One snippet that does not parse - two columns of method names inside a `python` fence | `scripts/check-api.py`, which reads **both** trees |
| `links.py` computed a page's anchors from *code-stripped* text, so a heading with inline code got a mangled anchor and no link to one could be written | fixed in `scripts/links.py` |
| `<CodeBlock code={`...`} />` survived a website-to-docs port as raw JSX | the converter turns it into a fence |

Zero self-links and zero stray absolute routes, checked the same way the site is.

## One thing the docs and the website disagree about on purpose

Java's website calls a section `Events`; Java's docs call the same section `ModelEvent`. Each
side of this port follows **its own** counterpart, so the pair disagrees and both are right.
`SECTION_OK` in `scripts/docs-website-sync.py` records it, with that reason.
