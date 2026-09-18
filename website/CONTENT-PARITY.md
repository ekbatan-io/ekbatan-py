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
cheapest signal for *where to look*. **done** is >=90% of headings and >=75% of the words.
Pages Java has and this port should not are listed in the script's `NA` with their reason, so
the number is never improved by pretending.

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

## What will never reach 100%, and why

- **`reference/multi-database`** - Java's three `Why ...` subsections are all about jOOQ
  converters, which SQLAlchemy's type helpers replace. Our dialect-disagreement table is the
  counterpart. Same for its `Repository field-definition pattern`.
- **`reference/events/streaming`** - Java documents two SMT variants, their configuration,
  payload encoding, envelope timestamps and error handling at length. That artifact runs inside
  Kafka Connect, in a JVM, whichever language wrote the row, so this port owes the row format
  and a pointer to it rather than a second copy of its manual.
- **`learn/complete-project-setup`** - Java's is inflated by its StackPicker rendering roughly
  36 build-file combinations (Gradle/Maven x 3 DI frameworks x 3 dialects). There are no build
  descriptors here to vary.
