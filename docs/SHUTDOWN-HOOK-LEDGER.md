# One shutdown switch, on by default, as Java's - ledger (Dracula 33)

The brief (the user, 2026-10-25, "do it", after the bite-sized explanation and the pros and
cons): one setting, named like Java's, `register_shutdown_hook`, on by default; `stop_on_exit`
goes away. When it is on: the app ends by itself - running jobs finish first, as before; the app
gets SIGTERM - if nothing else listens for it, ekbatan does, and the jobs finish, then the process
stops; if a server like uvicorn already listens, ekbatan stays out of the way; if the registry was
not started on the main thread, ekbatan cannot listen and the startup warning stays. Turning the
setting off means you stop the jobs yourself.

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "One shutdown switch, on by default
(EKB-0562-)". Status: `todo` / `doing` / `done`. A row is `done` only when a run shows it.

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | `JobRegistryBuilder.register_shutdown_hook(enabled)`, on by default, the argument required as Java's; `stop_on_exit` removed | done | `src/ekbatan/distributedjobs/__init__.py`; `JobRegistry(..., register_shutdown_hook=True)` |
| F2 | one SIGTERM handler for the process, taken in `start()` only where SIGTERM has its default and only on the main thread; logged in one line | done | `src/ekbatan/distributedjobs/_shutdown.py` `add()` |
| F3 | on SIGTERM: the default put back first, every hooked registry stopped, then the signal re-raised; `os._exit(143)` if the process is still alive (PID 1) | done | `_shutdown._on_sigterm` |
| F4 | `stop()` takes the registry off `atexit` and off the hook; the last one off gives SIGTERM back, if the handler is still ekbatan's and this is the main thread | done | `_shutdown.remove()` |
| F5 | a child made by `fork()` forgets the parent's registries | done | `_shutdown._forget_after_fork`, `os.register_at_fork` |
| F6 | the startup warning: once per process, when SIGTERM is still unhandled after the hook had its chance - two texts, hook off and started off the main thread | done | `_warn_if_sigterm_is_unhandled(scheduler, hooked)` |
| F7 | `build_manual()` never under the hook | done | `register_shutdown_hook=False` |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | real processes: a running job finishes on a normal exit, Ctrl-C (once) and SIGTERM by default; a second SIGTERM stops at once; an application's own handler keeps SIGTERM; turned off, a normal exit, Ctrl-C and SIGTERM cut the job off; a forked child leaves the parent's registry alone | done | `tests/integration/test_jobs_at_exit.py`, 17 tests |
| T2 | the warning's rules and texts, the handler taken or left | done | `tests/unit/test_sigterm_warning.py` |
| T3 | the handler's rules: taken only when free and on the main thread, one for every registry, given back by the last, left alone once replaced, stops every registry then re-raises and exits, survives a failing stop, forgets after fork | done | `tests/unit/test_shutdown_hook.py` |
| T4 | in this process: on by default, given back on stop, nothing when off, a real SIGTERM stops a real scheduler | done | `tests/integration/test_jobs.py::TestShutdownHook` |
| T5 | real processes: a bare worker takes SIGTERM and is not warned; with the hook off it is warned; a uvicorn service is neither | done | `tests/integration/test_sigterm_warning.py` |
| T6 | red on the old code, green on the new | done | old code: 20 failed, 10 errors, 12 passed, and the real-SIGTERM test killed pytest (exit 143); new code: all pass (R1) |

## Examples

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | the job workers' `run()` has no signal code, as Java's worker has none; `app.py` and the README say why | done | the two uv workers edited; `sync-examples.py` wrote the pip and Poetry copies; `--check`: in step |
| E2 | the REST and saga examples | done | unchanged: under uvicorn the server has SIGTERM, and the lifespan stops the jobs |

## Docs

| # | item | status | proof |
| --- | --- | --- | --- |
| D1 | the jobs reference, `docs/` and website, in step | done | `docs/jobs/distributed-jobs.md`, `reference/jobs/distributed-jobs.mdx`: the section rewritten, the table measured by T1 |
| D2 | startup and shutdown, `docs/` and website; going to production | done | three paragraphs in each copy of startup and shutdown; one in going to production |
| D3 | `DIVERGENCE.md`, `SHORTCOMINGS.md`, the decisions index row | done | the section is "matched"; the row is struck through, closed in `EKB-0562` |
| D4 | Draculas #32 and #33 matched, their D and E details | done | `docs/DRACULAS.md`, `docs/dracula/D-jobs-locks.md`, `docs/dracula/E-using-it.md` |
| D5 | the scheduler port's file ledger line, cut off mid-sentence, finished | done | `db-scheduler/docs/FILE-LEDGER.md` |
| D6 | parity-probe scripts | done | 8 scripts converted (`register_shutdown_hook(False)` for `stop_on_exit(False)`); each compiles; not re-run, their recordings kept |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| R1 | the new and changed tests | done | the five files: 44 passed (SQLite); `test_jobs.py` and `test_event_jobs.py` on PostgreSQL, MySQL and MariaDB: 113 passed |
| R2 | the whole suite, SQLite | done | 3316 passed, 109 skipped, 0 failed (before the turned-off Ctrl-C case was added; that case passes in R1) |
| R3 | lint: ruff, format, mypy, links, divergence, builders, sync-examples, check-api, docs-website-sync | done | all pass: mypy 224 files; links 716; every divergence triaged; 15 builders; 14 examples x 3 builds in step; check-api 0 wrong; 34 pairs, 0 below 80% |
| R4 | the two uv job workers' tests, and the real worker on SIGTERM | done | 4 and 4 passed. `job-worker-uv-pg` run against its compose databases and sent SIGTERM: it logged that it took SIGTERM, the two event jobs running at that moment logged "execution finished", the scheduler stopped, and the process died of SIGTERM (status 143) |

## Commits

| # | item | status | proof |
| --- | --- | --- | --- |
| C1 | EKB-0562 framework and tests; EKB-0563 examples; EKB-0564 docs and the rest | done | on main, not pushed |
