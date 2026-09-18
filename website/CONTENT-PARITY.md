# Content parity

`PARITY.md` counts whether a page **exists**. This counts whether it **says the same things**.

    cd website && npm run build
    cd ../../ekbatan/website && npm run build
    python3 scripts/content-parity.py

It compares the rendered text of both built sites, so it measures what a reader sees rather
than what the source looks like. Two numbers, both proxies:

| | |
| --- | --- |
| **§ match** | the share of Java's headings with a near-match in ours - structural drift |
| **words** | ours as a share of Java's - volume drift |

Neither is the goal. A page can sit at 100% on both and still read differently; they are the
cheapest signal for *where to look*.

Every page ends in one of four states, and two of them are held in the script so they cannot
rot into prose nobody rechecks:

| | |
| --- | --- |
| **done** | >=90% of headings, >=75% of the words |
| **capped** | the remaining gap is accounted for; `CAPPED` holds the reason |
| **n/a** | Java has it and this port should not; `NA` holds the reason |
| **partial** / **todo** | content is still owed |

The number is never improved by pretending: a `CAPPED` or `NA` entry names something checkable,
and a page in either still prints its real measurements.

Not wired into `npm run check`: it needs the Java site built next door, which CI does not have.

## Where the drift came from

Three causes, and they needed different fixes.

**The reference pages were converted from `docs/`.** They kept the docs tree's shape rather than
Java's, which is why `repository`, `transaction-manager`, `keyed-locks` and `multi-database` all
sat in the twenties *despite roughly correct word counts* - the content was there under
different headings. Mostly renames, plus promoting paragraphs that were already Java's
subsections in bold rather than as headings. `keyed-locks` had all three of Java's subsections
under `Where to acquire the lock`, just not marked up as any.

**The learn pages were written fresh.** Same ground, different order, about two thirds the
length. `getting-started` needed a real rewrite rather than renames: Java teaches the *problem*
first, across two numbered sections with `GuidedFlow` diagrams, then model, action, executor and
what happens after commit, each closing with a pointer to the page that goes deeper. Ours opened
straight into code. `sharding` needed its six steps reordered into Java's sequence.

**Some headings were written for a porter, not a reader.** `Three attributes Java does not have`,
`workers(n) - a knob Java does not need`, `One thing Java has to do that this port does not`.
Each is a true observation and none of them is a heading: they describe the comparison rather
than the content, and a reader who has never seen the Java site cannot use them. They now say
what is under them.

## Where a page stops

**done** is >=90% of headings and >=75% of the words. Five pages will not reach it, and the
script marks them **capped** with the reason inline, so the row still prints its real numbers and
a cap that stops being true is one somebody can disprove:

- **`reference/tables/events`** and **`reference/tables/event-notifications`** - each page is
  titled for the table as it exists here: `events`, unqualified. Java's are `eventlog.events`
  and `eventlog.event_notifications`, because its tables live in a schema of their own by
  default and these do not. One heading out of seven, and the heading is the page's own name.

- **`reference/action-plan`** - three method headings carry this port's names, `add(model)` /
  `add_all(models)` against Java's `add(entity)` / `addAll(entities)`. Renaming them to match
  would be a lie about the API.
- **`reference`** - seven of Java's cards are for things absent here (five DI integrations,
  annotations, native image) and two spell generics Python's way: `Model[ID, S]`, `Entity[ID]`.
- **`learn/complete-project-setup`** - Java's StackPicker renders 24 dependency blocks
  (2 build tools x 4 stacks x 3 dialects) and a jOOQ codegen section. Measured: those two
  sections alone are **47%** of that page's 16,504 rendered words, and neither has a counterpart
  with anything like that surface. The page went 19% -> 40% of Java's words once the content that
  *does* port was written; the rest is the picker.

## The matcher was too generous, once

Heading matching was a free substring test in both directions: Java's `Five backends` counted as
matched by our `Reentrancy contract - uniform across all five backends`, which contains those
words for an unrelated reason. That is how a section goes missing while its page reports 100%.

It is anchored at the front now - equal, or one is the other's 14-character prefix. Tightening it
moved two pages from **done** to **capped** and cost nothing else, which is roughly the ratio you
would hope for: the loose test was wrong twice in 42 pages, and both times about a page title.

## Two caps that turned out not to be caps

Both were recorded here as permanent and both were wrong. Worth keeping, because the failure mode
is the same each time: *a Java-shaped implementation detail was mistaken for a Java-shaped
concern.*

**`reference/events/streaming`** was excused as "the SMT runs inside Kafka Connect, in a JVM, so
this port owes the row format and a pointer rather than a second copy of its manual." The pointer
was the error. Connect is a JVM process **whichever language wrote the row** - so a Python
operator deploying Variant B needs exactly that configuration, and sending them to another site
for it is drift, not economy. All six sections now live here, with the Python-side facts checked
rather than assumed: the outbox writer inserts `delivered = False` and the fan-out job is the only
writer that sets it true, which is the invariant the SMT's unwrapped-record fallback depends on.
67% -> 100%.

**`reference/multi-database`** was excused as "Java's three `Why ...` subsections are all about
jOOQ converters, which SQLAlchemy's type helpers replace." True of the *mechanism* and false of
the *question*. Why MySQL wants `CHARACTER SET ascii` on a uuid column is a fact about MySQL;
why a uuid is `CHAR(36)` rather than `BINARY(16)` is a decision this port makes too, for the same
reason; and MariaDB reporting `JSON` as `LONGTEXT` breaks reflection here exactly as it breaks
codegen there. Only the *answer* to `Repository field-definition pattern` changed - `with_variant`
puts the per-dialect rendering inside the type, so there is nothing to select. 64% -> 100%.
