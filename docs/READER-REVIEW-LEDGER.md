# The website read as a learner - ledger

The brief (the user, 2026-10-30): read the website page by page as someone being onboarded
little by little, find what reads as written for machines rather than people, and improve or
simplify it. "Previously the java website was better" - so where a page has a Java counterpart,
that page is the yardstick.

## The test each page must pass

1. **The first lines say what the page is for**, in plain words.
2. **It builds on the pages before it.** A new term is explained where it first appears, or
   linked; nothing leans on a page that comes later.
3. **An example comes before the rule.** Code is short, and what is new in it is said.
4. **Short sentences, one idea to a paragraph.** No chains of dashes, semicolons and asides.
5. **Nothing written for maintainers or machines**: no ledger numbers (`EKB-...`), "Dracula",
   "parity", "the port", test names, internal file paths, script names, or the history of a
   decision - unless the reader needs it to use the thing.
6. **Java only where the two actually work together** (the user's rule for this site).
7. **Basics first, edge cases after**, marked as such - or moved to the reference.
8. **The length fits the job.** A Learn page teaches one thing; depth belongs in the reference.

Every change goes into the page's `docs/` twin as well, where it has one
(`scripts/docs-website-sync.py` holds the pairs). Status: `todo` / `doing` / `ok` (read, nothing
needed) / `fixed`.

## Home and the Learn path, in reading order

| # | page | words (Java's) | status | what a learner hit, and what changed |
| --- | --- | --- | --- | --- |
| H1 | `index.astro` (home) | 2114 | fixed | the hero still said v0.1.0 after the 0.1.1 release (the version test now checks it); "interoperate on the same tables" was wrong - the two ports never share a database - and its link pointed at this repository instead of the Java one; the repository card listed a `get` that does not exist; "forgetting is not expressible" |
| L0 | `learn/index.astro` (the path) | 165 | fixed | two card blurbs: "collaborator" / "what actually contends", and "this port" |
| L1 | `learn/getting-started.mdx` | 1280 (1213) | fixed | good shape, Java's length. Explained the three types in `Model[...]`, `principal` and `.value` where they first appear; merged two sentences that said the same thing |
| L2 | `learn/the-dual-write-trap.mdx` | 611 (694) | fixed | "OOM-killed", "the pod is rescheduled", "write-ahead log" and "cross-shard" before sharding was taught - plain words instead; twin too |
| L3 | `learn/outbox.mdx` | 1044 (665) | fixed | four full `CREATE TABLE` statements with deep per-database notes ("that was checked") in a concept page; rewritten in Java's shape - what a row holds, in plain words, with a link to the exact columns: 1044 -> 678 words (Java 665). The twin had its trap section twice, and "the test suite fell into this"; merged |
| L4 | `learn/models-and-entities.mdx` | 3150 (1421) | fixed | a reference page in disguise: the JSON table for every field type, the builder generator's internals, a repo script name, base classes and `__post_init__`; cut to Java's weight, 3150 -> 1487 words (Java 1421), with links for the rest. Two errors fixed: the factory was called `open` beside code saying `create_wallet`, and the action naming rule contradicted its own examples. The JSON table moves to the ModelEvent reference (R6) |
| L5 | `learn/actions.mdx` | 1888 (1454) | fixed | same sections as Java's, same density; kept the structure, rewrote ContextVar / single-writer / DBAPI / "correct-looking traceback" / "cause chain" in plain words, and a sentence pointing at "the rule" without saying which; twin too where it shares the text |
| L6 | `learn/your-first-action.mdx` | 1830 (1025) | fixed | broken box borders in the opening diagram; one dense paragraph leaning on schemagen two pages before it is taught; "seam"; a three-link aside on time; the "Next" link skipped the path's next page |
| L7 | `learn/install.mdx` | 729 (-) | fixed | "a checker sees only `__getattr__`", schemagen "reflects the result", a TOML section called a "table"; and it never said the generator needs Docker running; `docs/getting-started.md` too |
| L8 | `learn/sync-or-async.mdx` | 761 (-) | fixed | "the wait releases the GIL"; a section on handler loops two pages before handlers are taught; twin too |
| L9 | `learn/wiring-a-service.mdx` | 1042 (-) | fixed | a quip about config files, "yoyo" unexplained, protobuf syntax as the reason for the namespace rule, "8, not 3" next to a `workers + 3` table, and history ("did, while the examples were being written", a measurement) |
| L10 | `learn/consuming-events.mdx` | 2491 (2395) | fixed | "WAL/binlog", "fan-out" before it is explained, "cluster exclusivity, heartbeating and crash recovery are inherited", "cluster-stable", "drives deserialisation", "canonical name", "non-terminal rows", "sentinel", "Variant A/B" (another page's terms), "check-then-act has a window", "tailing" / "backfill" |
| L11 | `learn/sharding.mdx` | 1265 (1216) | fixed | the compose file named both databases `wallet_repository` - a bad field rename - and the `psql` lines did not match it; now the example's real `wallet_global` / `wallet_mexico`; "rand_b field of a UUIDv7", "bonded", "policy/capacity axis", WAL, "step four of four" on a six-step page, a fragment bullet, "scatters", "predicate read", two See-also links to one page |
| L12 | `learn/sharding-strategies.mdx` | 1248 (1321) | fixed | "cardinality", "conflate", "failure domain", "VPC", "compliance posture", "scatter accessors", "scatter-gather", "CQRS materialisations", "bonded"; twin too |
| L13 | `learn/sagas.mdx` | 1781 (1281) | fixed | said three things two or three times each - a failure is an event, idempotency by constraint, no coordinator - and the status endpoint twice; merged in both copies |
| L14 | `learn/async.mdx` | 1215 (-) | fixed | a shutdown order packed into one parenthesised sentence (now a list), "libpq spells it ... server_settings", a section numbered 7b, a run-on intro; twin too |
| L15 | `learn/going-to-production.mdx` | 1140 (-) | fixed | `wallet_repository.get` does not exist (`get_by_id`); a string of timings (354 ms to 48 ms, 631 against 631), "one UPDATE per bucket", "as Java's does", "the GIL is released", PgBouncer unexplained |
| L16 | `learn/startup-and-shutdown.mdx` | 8532 (-) | fixed | written well for people - it explains every term and says how to read it - but 8,500 words; kept, with "The short version" added at the top in both copies. Fixed the same `wallet_repository.get` slip, and one Java comparison |
| L17 | `learn/complete-project-setup.mdx` | 6667 (8083) | ok | a recipe whose files are the tested examples' own and whose prose says why each step is there; nothing machine-like |

## The reference

| # | page | words | status | what a learner hit, and what changed |
| --- | --- | --- | --- | --- |
| R0 | `reference/index.astro` | 831 | fixed | jargon blurbs (optionality matrix, SMTs, facade, policy axes, staging API, cluster-exclusive) in plain words; the multi-database card said "routing actions across several databases", which that page is not about |
| R1 | `reference/action.mdx` | 1528 | fixed | a good reference; "in Java's order", "a partial write is not expressible", a paragraph on why the plan is "ambient" ("threading it through every signature"), and the retry rules in plain words; twin where shared |
| R2 | `reference/action-plan.mdx` | 809 | fixed | "the GIL is not the reprieve it looks like ... a correct-looking traceback" in plain words, both copies; "sentinel row" where the page itself explains the row |
| R3 | `reference/action-executor.mdx` | 1119 | fixed | the `execute` signature was wrong (action first, keyword `principal`; the code is `execute(principal, action, params, /, config=None)`), the constructor listed an `events` argument that no longer exists and called instances optional where they are refused; a call example first; "sentinel" explained; a Java comparison out |
| R4 | `reference/model.mdx` | 1815 | fixed | a solid reference; its `deposit(..., at: datetime)` example now takes an `Instant`, as every other page does - twin too |
| R5 | `reference/entity.mdx` | 948 | fixed | "the action's sentinel row", "threads the version forward ... the successor version its optimistic-locked update needs", "is not expressible" |
| R6 | `reference/model-event.mdx` | 609 | fixed | basics first; "three places" that named two; "envelope", "correlates" in plain words. It sent readers to the Learn page "for the full table" just as that table left the Learn page - the table now lives here, with the rules that went with it |
| R7 | `reference/repository.mdx` | 3276 | fixed | a jargon lead; the opening example lacks `to_record`/`from_record` and would be refused - now said, and linked; EKB-0480/EKB-0217 history out; "terminals", "invariant", "bridge", "predicate helpers"; the "perform() is not transactional" warning twice, merged |
| R8 | `reference/transaction-manager.mdx` | 2033 | fixed | the website lead was cut off mid-word; a link to the wrong anchor; "Two consequences" followed by five bullets; "a worked example" with no example; "ambient", "primitive", "pool pair" |
| R9 | `reference/wiring.mdx` | 2693 | fixed | a false paragraph about an `events` argument that no longer exists; `clock=running` eleven times with `running` defined nowhere; "annotations" meaning decorators; history; the jobs pool rule said "twice the workers" where the code needs `workers + 3` |
| R10 | `reference/wiring-fastapi.mdx` | 1988 | fixed | the same `wallet_repository.get` slip, and `service.wallets` left by the rename; it never said a sync call in `async def` raises `BlockingCallError`; asides ("It cost an afternoon", "seam", "born and buried") |
| R11 | `reference/config-files.mdx` | 1239 | fixed | the docs twin had no opening line; a sentence beginning "- or" rendered as a stray bullet; a nine-line paragraph on env-var types now one rule and three bullets |
| R12 | `reference/multi-database.mdx` | 2768 | fixed | said the framework's tables build an index they do not declare; `_CanonicalUuid`, a private name, in a snippet; CI and suite detail out; "naive" defined |
| R13 | `reference/sharding.mdx` | 3257 | fixed | four false statements, each checked by running: when a strategy that cannot route from an id fails, the warning "under either policy", `effective_shard(key)`, and `readonly_db(key=wallet_id)` with a typed id (raises - it needs `.value`); long configuration split; a test-file mention out |
| R14 | `reference/migrations.mdx` | 3266 | fixed | basics first; `MIGRATIONS`, `layout` and `single_database` were never defined; said `migrate_registry` makes `eventlog` only on PostgreSQL (MySQL and MariaDB too); `run_migrations` is the examples' setting, not the framework's |
| R15 | `reference/schemagen.mdx` | 6683 | fixed | 6,700 words, reordered so a reader meets what it is and how to run it first (now says Docker is needed), the rare material last; history and test detail out; the example error's format numbers match the code |
| R16 | `reference/keyed-locks.mdx` | 3366 | fixed | rules said twice ("max_hold is not a timeout", "busy is not failure") merged; the code used `locks` before saying how to build one; "a mistake this framework once made" and a decision-record table out |
| R17 | `reference/jobs/distributed-jobs.mdx` | 2774 | fixed | never said what `name()`, `schedule()` and `execute()` are; a Java JVM paragraph and "while these examples were being written" out; the table section repeated the coordination section and called the method `run()`; a rule on workers contradicted the default |
| R18 | `reference/events/local-event-handler.mdx` | 3215 | fixed | the website lead stopped mid-word; `&lt;=` showed literally; the EKB-0532 rename had turned `wallets` and the topic `wallets.deposits` into `wallet_repository...`, restored; history ("This page used to say the opposite") out; the publisher topology was numbered third of four, it is second |
| R19 | `reference/events/streaming.mdx` | 4548 | fixed | reordered: everything a consumer needs first, then the Kafka Connect part, with a line saying which reader needs which; "SMT" spelled out; a second diagram and a duplicate section folded; claimed `from_row` converts values (it does not, `from_message` does); now says what JSON with in-process handlers needs (`skipped.operations`) |
| R20 | `reference/runtime/async.mdx` | 3243 | fixed | semicolon chains to lists; `ContextVar`, libpq, pre-ping, p50/p99 explained; a script mention out |
| R21 | `reference/runtime/concurrency.mdx` | 2754 | fixed | the same `wallet_repository.get` slip; the GIL, greenlets and the scheduler's loops explained; history ("used a thread per hold until measured"), "631 passed" and a test-file link out |
| R22 | `reference/runtime/observability.mdx` | 1506 | fixed | a CI paragraph out; the Java note moved to where shared dashboards matter; the partial-commit table got a header |
| R23 | `reference/tables/index.mdx` | 900 | fixed | a lead that ended on a dangling colon; the upgrade-only sections marked as such |
| R24 | `reference/tables/events.mdx` | 1274 | fixed | "sentinel row" defined; the MySQL DDL lacked the indexes the intro promised; a test-suite anecdote now advice |
| R25 | `reference/tables/event-notifications.mdx` | 1195 | fixed | the PostgreSQL DDL showed MySQL's index; "twelve copied columns" listed eleven; "idempotent", "check-then-act" in plain words |
| R26 | `reference/tables/scheduled-tasks.mdx` | 1080 | fixed | the jobs pool said "at least twice the worker count", the code warns below `workers + 3`; a DDL index left out "for length" restored; "a port with its own tests" and CI sentences out |
| R27 | `reference/dialects/postgresql.mdx` | 880 | fixed | kept parallel with the other three; per-session UTC setting added, as MySQL has `init_command`; "322 integration tests" and "that was checked" out |
| R28 | `reference/dialects/mysql.mdx` | 1026 | fixed | "reserve TIMESTAMP for db-scheduler's own table" was wrong - that table is `DATETIME(6)`; maintainer sentences out |
| R29 | `reference/dialects/mariadb.mdx` | 1025 | fixed | claimed the framework picks its lock provider from the dialect - nothing does, you build `MariaDBKeyedLockProvider`; the url section leads with the rule |
| R30 | `reference/dialects/sqlite.mdx` | 1030 | fixed | said the SQL keyed lock depends on `SKIP LOCKED` - only the scheduler does; "event-sourced service" in plain words |
| R31 | `reference/beside-java.mdx` | 1519 | fixed | lists for the Redis lock layout, the time rules and the schema files; "one database per service" now says a server each on MySQL and MariaDB |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| C1 | `make lint` (docs/website sync, links, API claims) after the edits | done | exit 0: 34 pairs, 0 below 80%; links resolve; 554 snippets and 193 imports, 0 wrong; and every one of 267 anchor links resolves (a planted bad one is caught) |
| C2 | the website builds and `npm run check` passes | done | 59 pages built; 52 internal targets, 0 broken; unit suite 2253 passed |

## How it was done

The Learn path, the home page and the four Reference pages that share a docs file with it were
read and rewritten by the lead, in order. The other 27 Reference pages went to four reviewers
working in parallel, one group of files each, under the test above; the lead then checked their
changes - the claimed fixes against the code, every anchor across the site, and the pages that
moved most. Facts a reviewer corrected were checked by running the code.

What is left, and is not the website: the framework's own error messages still say "in Java's
order" and "as Java's takes its class" (`src/ekbatan/action/executor.py`).
