# N6 - what the docs and website promise, checked by running it

Scope: ekbatan-py's user-facing docs (`docs/` without the ledgers and parity files, `README.md`,
`AGENTS.md`, `agent-skills/`, `website/src/pages`) and `DIVERGENCE.md`. Every concrete claim
("X does Y", "as Java does", snippets, config examples, commands) was checked by running it against
today's `main` (after EKB-0314): SQLite for most, and PostgreSQL 17, MySQL 8.4, MariaDB 11.4 and
Redis 7 in containers (ports 58700-58762, all removed) where a claim depends on the database. No
Java test was needed: every "as Java does" claim checked here was already proven by a round-1 probe
or the parity-fix ledger, or was read in the Java source (marked `read`). Each docs page was also
compared with its website copy sentence by sentence (`sentence_diff.py`). About 220 claims checked.
Probes and outputs: `parity-probes/round2-N6/` (`migrations/`, `locks-jobs/`, `events/`, `core/`,
and the top-level scripts).

**Leftovers from the reverted work: none.** No page still describes the Flyway-compatible migrator
(EKB-0277..0283), its callbacks, placeholders, `classpath:` locations or `flyway_schema_history`,
or the 50 s lock limit (EKB-0313). `post-apply` hooks are described as they work today. The one
50 s remnant is item 18 below. What is wrong is mostly older: docs not updated when earlier changes
landed (repositories needing `from_row`/`to_row`, `plan.add` no longer returning a stamped copy, the
in-process lock removed, the event indexes moved out of `emit_ddl`, fan-out made concurrent).

| # | Difference (plain, one line) | Java | Python | Verdict | Why (one line) | Evidence |
|---|---|---|---|---|---|---|
| 1 | A connection the pool opened ahead of time is not checked before its first use, so after a database restart the first request on it fails | Hikari checks a connection on borrow once it has been idle over 500 ms | `pool_pre_ping` is skipped on connections the EKB-0305 filler opened; 1 of 3 checkouts failed in 5 of 5 runs | needs you | the doc (`pool_pre_ping`: "check a connection is alive before handing it out") is right; the code broke it | `locks-jobs/pool_preping_one.out`, `pool_preping.out` |
| 2 | Getting started and many pages write a repository with no `from_row`/`to_row`; it is refused when run | - | `TypeError: WalletRepository cannot be created: from_row() and to_row() have not been written` | fix | stale since EKB-0217; the first thing a new user copies fails | `core/getting_started.out` |
| 3 | The jobs page's own example job does not run (`name = "pay-stipend"`) | - | `TypeError: 'str' object is not callable`; `name` is a method | fix | first snippet on the page fails | `locks-jobs/jobs_claims.out` |
| 4 | Model snippets build a model with no `created_date` (some with no initial version) | - | `build()` refuses: `created_date cannot be null` | fix | stale since EKB-0292 | `core/model_claims.out`, `core/sharding_claims.out` |
| 5 | Pages say passing an action instance needs no registry | `execute` takes a class | an instance is refused: "execute takes the action's type" | fix | contradicts the same page and the code | `core/executor_claims.out` |
| 6 | Pages say `plan.add`/`plan.update` return a stamped copy | returns the object passed | returns the object passed; `update` leaves `updated_date` alone | fix | stale since EKB-0192 | `core/executor_claims.out`, `core/repo_claims.out` |
| 7 | README's testing example `assert staged.updated(Wallet) == [staged.result]` fails | - | the plan holds the row at its read version, `result` the next version | fix | a copied assertion fails | `core/getting_started.out` |
| 8 | The learn page says retry matching is by `isinstance`; the code and other pages say exact class | exact class | exact class (`EkbatanError` policy does not catch `StaleRecordError`) | fix | stale since EKB-0198; two pages contradict | `retry_match.out` |
| 9 | Pages say the framework creates the event indexes and tell you to "drop the generated index" | partial index in Java's migration | `emit_ddl` creates no index on any dialect | fix | stale since EKB-0215; pages contradict themselves | `events/e1_ddl.out` |
| 10 | Pages say the in-process lock exists and that there are "five backends" | four providers | four providers (PG, MySQL, MariaDB, Redis); no `inprocess.py` | fix | removed in EKB-0225; same page says "there is no in-process provider" | `locks-jobs/locks_claims.out` |
| 11 | Pages say handlers on one event, and handlers in a batch, run one after another | concurrent | concurrent (max 2 overlapping; 4 handlers meet at a barrier) | fix | stale; the same pages also say "parallel" | `events/e2_pipeline.out`, `locks-jobs/dispatch_concurrent_test.out` |
| 12 | Pages say fan-out visits shards one at a time, and that Java forks a thread per shard for scatter reads | fan-out concurrent; scatter reads sequential | fan-out concurrent (2 shards x 0.5 s took 0.50 s); scatter reads sequential | fix | stale since EKB-0203, and wrong about Java's reads | `core/fanout_threads.out`, `events/e2_pipeline.out` |
| 13 | The pool size rule for jobs says "twice the worker count" on some pages and `workers + 3` on others | - | db-scheduler asks for workers + 3 and warns below it | fix | pages contradict; "twice" is too small for 1-2 workers | `locks-jobs/jobs_pg.out` |
| 14 | The website's Avro example uses a schema key the connector rejects | `namespace + ".avro." + eventType` | - | fix | copying it fails with "No Avro schema configured" (`read`, Java source) | `PayloadSchemaBindings.java:93-95` |
| 15 | The website says a handler running longer than the poll delay is picked up again | - | only a stopped heartbeat (60 s) does that | fix | contradicts the reference page and the code (`read`) | `local-event-handler.md:316` |
| 16 | `ActionEvent.from_row` is said to convert decimals and timestamps | - | it leaves a float and a string; only `from_message` converts | fix | consumer code would get the wrong types | `events/e3_streaming.out` |
| 17 | Codegen output shown as `builds = Wallet`; it now writes `builds_ref = "module:Wallet"` | - | `builds_ref` with quoted type parameters | fix | docs show a file codegen does not write | `core/codegen_builders.out` |
| 18 | DIVERGENCE says Flyway gives up after 50 s with no database named | PostgreSQL only; MySQL waits for ever | waits for ever on every database | fix | contradicts migrations.md and the user's decision record | `migrations/m3_pg_mysql.out` |
| 19 | "Three attributes Java does not have" on a partial cross-shard commit | Java sets the same three on its span | same three | fix | false (`read`, `ActionExecutor.java:312-314`) | `docs/runtime/observability.md:67,77` |
| 20 | README says SQLAlchemy and db-scheduler are the only required dependencies | - | also PyYAML and opentelemetry-api | fix | install docs incomplete | `readme_counts.out` |
| 21 | README test counts are stale (250 / 322 / 578 / 1,370) | - | 922 / 507 / 1,436 / 2,708 | fix | numbers from months ago | `readme_counts.out` |
| 22 | "631 passed under gevent" in concurrency docs and DIVERGENCE | - | the suite now has 1,436 tests (gevent run not repeated) | fix | stale number | `locks-jobs/test_count.out` |
| 23 | Jobs docs and DIVERGENCE call the job method `run()` | - | it is `execute()` | fix | wrong name (`read`, `distributedjobs/__init__.py:94`) | - |
| 24 | Sample warning shows logger `ekbatan.persistence` | `io.ekbatan.core.shard.DatabaseRegistry` | `io.ekbatan.core.shard.DatabaseRegistry` | fix | renamed in EKB-0248 | `core/sharding_claims.out` |
| 25 | Sharding page lists `delete` among methods that route by id | - | there is no `repo.delete` | fix | another page says so | - |
| 26 | Sharding page says a non-id strategy "raises at construction" | - | raises at the first id call, as the same page says elsewhere | fix | page contradicts itself | `core/sharding_claims.out` |
| 27 | `attempts` is "failed attempts so far" | success adds 1 | success adds 1 | fix | wrong column description | `events/e2_pipeline.out` |
| 28 | Discards are said to log "an example event id" | - | the log names the notification id, one line per row | fix | wrong log description | `events/e2_pipeline.out` |
| 29 | The learn page says plan, clock and principal are all in a ContextVar | only the plan | only the plan | fix | contradicts the concepts page | `core/executor_claims.out` |
| 30 | AGENTS and the maintainer skill list `locks/inprocess.py` and a `ShardConfig` class | - | neither exists | fix | stale module map | - |
| 31 | `migrations.md` says "every example has this test" (`validate_scripts`) and that every example migrates from files | - | the three SQLite examples use `create_all` and have no migrations | fix | small overclaim | `migrations/m1_sqlite_claims.out` |
| 32 | The `info` states list misses three states | - | also `Failed (Missing)`, `Failed (Future)`, `Ignored (Baseline)` | fix | incomplete list | `migrations/m1_sqlite_claims.out` |
| 33 | Leftover sentence "Two halves, and only one of them ported" under a heading saying both match Java | - | both match | fix | contradicts its heading | `local-event-handler.md:275`, DIVERGENCE ~1028 |
| 34 | Whole sections appear twice in docs/ but once on the website | - | - | fix | copy-paste damage in 8 docs pages | `sentence_diff.out` |
| 35 | Small wording slips: "third of the four topologies", "three attributes" vs "two", bucket order listed two ways, README `evolve` vs `copy()`, logger table missing the pool and concurrency loggers, "full context" per shard | - | - | fix | each one line | the fork outputs |
| 36 | Pages differ from their website copy by small additions (website-only paragraphs in keyed-locks, transaction-manager, tables index; docs-only paragraphs in outbox) | - | - | leave | additions, not contradictions | `sentence_diff.out` |
| 37 | Removing a file's last newline counts as an edit, where the page says line endings do not | Flyway reads lines and would not notice | refused as edited | leave | the sentence is about CRLF; rare | `migrations/m2_lineendings_gone.out` |
| 38 | The pool does not reopen a thrown-away connection at once, as the page says | Hikari refills in the background | reopens at the next checkout | leave | costs one connect; soften the wording if touched | `locks-jobs/pool_refill.out` |
| 39 | Config errors are formatted two ways (`x.yaml.ekbatan...` vs `x.yaml: ekbatan...`) | - | - | leave | cosmetic | `core/config_files_claims.out` |

## Where each `fix` and `needs you` is

**1 (needs you).** Doc: `docs/database/sharding.md:89`. Python: `src/ekbatan/persistence.py:448` (filler thread) and `:530` (`_fill`); SQLAlchemy skips the ping on a connection still marked new (`sqlalchemy/pool/base.py:1299-1322`). Java: HikariCP's borrow check. Cost: probably small (clear the new flag on connections the filler adds, then rerun the probe), but it is code in the pool that EKB-0305 just changed, so it is yours to approve.

**2.** `docs/getting-started.md:135-143`; `docs/database/repositories.md:7-15`; `docs/wiring.md:61-63`; `docs/database/sharding.md:212-226`; `README.md:118`; `AGENTS.md:36`; `agent-skills/ekbatan-py-maintainer/references/architecture-map.md:47`; website `learn/your-first-action.mdx:104-111`, `learn/complete-project-setup.mdx:770`, `learn/getting-started.mdx:217`, `reference/index.astro:34`, `reference/sharding.mdx:255`. Cost: add the two methods to each snippet.

**3.** `docs/jobs/distributed-jobs.md:11`, website `reference/jobs/distributed-jobs.mdx:12`. One line.

**4.** `docs/concepts/models-and-entities.md:29-46`, `learn/models-and-entities.mdx:~30-45`, `docs/database/sharding.md:162-178` and its website copy. Add `created_date` and `with_initial_version()`.

**5.** `website/src/pages/reference/action.mdx:57-58`, `reference/action-executor.mdx:59`. Delete the sentence.

**6.** `docs/concepts/actions.md:53,128`; `learn/actions.mdx:57`; `reference/action-plan.mdx:44,52-53`; `docs/database/repositories.md:71`; `reference/repository.mdx:72`.

**7.** `README.md:244-249`. Compare ids, or compare to the read row.

**8.** `website/src/pages/learn/actions.mdx:185-188`; `agent-skills/ekbatan-py-maintainer/references/architecture-map.md:60` (and its `.claude`/`.agents` copies). Code: `src/ekbatan/action/executor.py:75-86`.

**9.** `docs/database/tables/events.md:40-41,119,143-150,184-205`; `tables/event-notifications.md:43`; `docs/events/local-event-handler.md:174-191`; `docs/database/multi-database.md:157-160`; `postgresql.md:101-104`; `mysql.md:121-122`; and each website copy.

**10.** `docs/database/keyed-locks.md:159,161,190,195-196`; `reference/keyed-locks.mdx:131,160,162,191,196`; `reference/index.astro:37`; `README.md:120`.

**11.** `docs/events/local-event-handler.md:331` (website `:329`); `DIVERGENCE.md:1362-1363`. (`src/ekbatan/events/dispatch.py:147-150` has the same stale sentence in a docstring.)

**12.** `docs/concepts/sharding.md:113-121,130`; `docs/database/sharding.md:434-444`; `reference/sharding.mdx:436-439`; `learn/sharding.mdx:210-212`. Java: `AbstractRepository` reads shards with `dbs().stream()` one after another.

**13.** "twice": `docs/database/tables/scheduled-tasks.md:106` (+mdx), `docs/wiring.md:280` (+mdx), `agent-skills/ekbatan-py-examples/SKILL.md:53`, `learn/complete-project-setup.mdx:345`. Change to `workers + 3`.

**14.** `website/src/pages/learn/consuming-events.mdx:224`. Use `<namespace>.avro.MoneyDepositedEvent`.

**15.** `website/src/pages/learn/consuming-events.mdx:309`.

**16.** `docs/events/event-streaming.md` ("Consumer-side envelope contracts") and `reference/events/streaming.mdx`.

**17.** `docs/concepts/models-and-entities.md:330,352`; `learn/models-and-entities.mdx:197,219`; `README.md:175`.

**18.** `DIVERGENCE.md:1150`. Add "on PostgreSQL".

**19.** `docs/runtime/observability.md:67,77`; `reference/runtime/observability.mdx:77`.

**20-22.** `README.md:68-71,202-203,269`; `docs/getting-started.md:39`; `learn/install.mdx`; `docs/runtime/concurrency.md:245` (+mdx `:247`); `DIVERGENCE.md:1424`.

**23.** `docs/jobs/distributed-jobs.md:293,296` (+mdx `:294,297`); `DIVERGENCE.md:695`.

**24-26.** `docs/database/sharding.md:353-355`, `:231`, `:333-335`, and the website copies.

**27-28.** `docs/database/tables/event-notifications.md:110` (+mdx `:111`); `docs/runtime/observability.md` alert sections (+mdx `:181`).

**29.** `website/src/pages/learn/actions.mdx:124`.

**30.** `AGENTS.md:71,83`; `agent-skills/ekbatan-py-maintainer/references/architecture-map.md:23,34`.

**31-32.** `docs/database/migrations.md:121,350,271-273` (+mdx `:124,353,274-276`).

**33.** `docs/events/local-event-handler.md:275` (+mdx `:273`); `DIVERGENCE.md` ~1028.

**34.** `docs/database/repositories.md:147-170`; `docs/runtime/observability.md:177-238`; `docs/concepts/outbox.md` ("One trap" / "Ordering"); `docs/database/sharding.md:436-444`; `docs/database/tables/events.md`; `tables/event-notifications.md`; `tables/scheduled-tasks.md:46/50,140/153` (its anchor at `:75` also differs from the website's); `docs/events/local-event-handler.md`.

**35.** `docs/events/local-event-handler.md:62,263/302,411`; `learn/consuming-events.mdx:143`; `README.md:116`; `docs/runtime/observability.md:94-106`; `docs/concepts/actions.md:194`; `docs/database/sharding.md:396-400`.

Every doc fix is an edit to text only; none changes behaviour.

## Totals

About 220 checked, about 180 same (true as written), 35 fix, 4 leave, 1 needs you.
