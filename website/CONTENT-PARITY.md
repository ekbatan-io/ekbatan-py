# Content parity

`PARITY.md` counts whether a page **exists**. This counts whether it **says the same things**.

    cd website && npm run build
    cd ../../ekbatan/website && npm run build
    python3 scripts/content-parity.py              # per page
    python3 scripts/content-parity.py --sections   # per section, within the pages

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

## The page view goes green long before the pages are right

Every page reached **done**, **capped** or **n/a**, and then `--sections` found **59 sections**
that were thin or missing anyway. A page total averages a gap away: `reference/keyed-locks` sat
at 100% of headings with `Five backends` at 58 words against Java's 491, because the eleven other
sections around it were fine.

That is the whole reason for the second view. It compares each of Java's headings against the
words under ours - **subsections included**, since a `## DDL` whose body all sits under
`### PostgreSQL` otherwise reports zero and reads as missing - and lists anything under 60% or
absent, worst first.

What closing those 59 actually added, since "the number went up" is not an outcome:

- the SMT manual, six sections of it, which had been a link to the Java site
- `CHARACTER SET ascii`, `CHAR(36)` vs `BINARY(16)`, and why MariaDB `JSON` needs forcing
- what the generator writes and what it deliberately does not
- what the decoder does with each annotation - **verified by running it**, including the two
  limits (an unknown key is dropped; a multi-arm union stays raw)
- the schema a migration actually creates, per dialect, on four pages that had one dialect
- `Action.perform()` is not transactional, which is the sharpest footgun on the repository page
  and was missing from it

Three claims written during that pass were **wrong and caught by running the code**: that the
provider's `close()` disposes its engine (it releases leases), that the accessors' routing
arguments are mutually exclusive (first non-`None` wins), and that the saga example has a
transfer state column (its state is derived from step markers). One more - a third index being
PostgreSQL-only - was disproved by generating the DDL. The rule that keeps paying: write the
claim, then run the thing.

## What the parity numbers still cannot see

Both views can be green while the site is wrong in ways neither measures. Four of those were
found by looking instead of counting, and each one is now checked by something:

| Found | Now checked by |
|---|---|
| 3 Python snippets that do not parse - one a method chain a reader would copy and get a `SyntaxError` from | `website/scripts/check-snippets.mjs`, in `npm run check` |
| 13 relative links that resolve outside the site and 404 once deployed, plus 1 `See also` pointing at its own page | `website/scripts/check-links.mjs` |
| Every claim the site makes about this API - each `from ekbatan... import X`, each `Class.attr` | `website/scripts/check-api.py`, in `make lint` |
| The defaults the site tabulates - pool sizes, poll delays, the retry count | `tests/unit/test_documented_defaults.py` |

And two the browser found, which no text comparison could:

- **11 paragraphs across 8 pages scrolled the whole page sideways at phone width**, because a
  fully-qualified name in inline code has nothing to break at. Fixed for the site at once with
  `overflow-wrap: anywhere`, plus `box-decoration-break: clone` so a broken fragment keeps its
  border.
- **Wide tables were wrapped by a script at load**, one frame too late and never with JavaScript
  off. `rehype-table-scroll` does it at build time now.

## `docs/` fell behind while this was happening

The website is not the source of truth; `docs/` is, and the drift ran one way. Measured after the
Java-parity pass: `docs/events/event-streaming.md` held **19%** of its page - the whole transform
manual existed only on the website - and five more sat between 37% and 72%.

All nineteen pairs are level now, and `scripts/docs-website-sync.py` fails `make lint` when one
drops below 80% of its counterpart. Word count is a crude proxy and a sufficient one: a page does
not lose a third of its words to formatting.

Porting them also exposed a bug in `scripts/links.py` - it computed a page's anchors from
*code-stripped* text, so a heading containing inline code got a mangled anchor and no link to one
could be written. It reads the raw text now.

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
