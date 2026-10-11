# Ledger: the whole `ekbatan` section from a file, and every example on it (Track 3)

Asked on 2026-10-11: every example reads its database layout and event-handler settings from a
config file, the way the Java examples read `application.yml`. Track 2
([CONFIG-FILES-LEDGER.md](CONFIG-FILES-LEDGER.md)) built the reader for `ekbatan.sharding`; this
track reads the rest of Java's `ekbatan` section and moves the examples over.

## What Java's examples put in the file

| Key | Java examples | Here |
|---|---|---|
| `ekbatan.namespace` | all | read; Java's default `default` |
| `ekbatan.sharding` | all | already read (Track 2) |
| `ekbatan.local-event-handler.*` | all (`handling.enabled: true`) | read into `LocalEventHandlerConfig` |
| `ekbatan.jobs.*` | job worker (`pollingInterval: PT10S`) | read into `JobsConfig` |

## Rules

| Part | Rule |
|---|---|
| Entry point | `load_ekbatan_config(path, *, environ=None)` returns `EkbatanConfig(namespace, sharding, jobs, local_event_handler)`; `load_sharding_config` unchanged |
| Durations | ISO-8601 text, exactly what Java's `Duration.parse` accepts (`PT10S`, `P1DT2H`, `PT0.5S`); `10s` refused, as Java's binder refuses it |
| Keys | kebab and camel, as in Track 2 |
| Unknown keys under `ekbatan` | refused with their path; keys outside `ekbatan` left alone |
| Missing `local-event-handler` / `jobs` | framework defaults, as Java |
| Example files | YAML, keys spelled as the Java examples spell them; url and login as `${...}` placeholders with local defaults, so a test fills them from its container |
| Example settings that Java has no key for | job worker threads, run-migrations: stay environment variables |

## Tasks

| # | Task | State |
|---|---|---|
| 1 | Duration reader (Java's `Duration.parse` grammar) | done - same result as `Duration.parse` on 20 inputs |
| 2 | `local-event-handler` and `jobs` readers; namespace; unknown keys refused | done |
| 3 | `load_ekbatan_config`, `EkbatanConfig`, exported | done - from `ekbatan.config_files` |
| 4 | Unit tests: every format, every key, durations case by case, refusals | done - `tests/unit/test_ekbatan_config_file.py` |
| 5 | Examples: `ekbatan.yaml` in each of the 7, app reads it, tests fill placeholders | done - each has a test that the file's settings are the running ones |
| 6 | Example READMEs and `__main__.py` | done |
| 7 | Docs + website: config-files page, wiring page, DIVERGENCE, parity ledger, decisions | done - also the setup tutorial |
| 8 | Prove the tests can fail | done - 11 bugs, all red |
| 9 | `make lint`, `make test`, `make test-all`, `make examples` | done - 2,345 passed on four databases, 765 unit, 92 example tests, website built |

## Judgement calls (also logged in DECISIONS-TO-REVIEW.md)

- Unknown keys under `ekbatan` are refused; Spring ignores them.
- A bare number is not a duration.
- Example files keep the Java examples' spellings, mixed as they are (`defaultShard`, `local-event-handler`).
- url and login are placeholders with local defaults; Java's files write the values.
- MySQL and MariaDB set `init_command` - PyMySQL's counterpart of `sessionVariables`.
- No `local-event-handler` block in the sharded example: nothing there reads it (Java's has a handler).
- The saga example runs on the event jobs with handling on, as Java's (the user's call); its test
  shares one running service and waits up to 15 s, as Java's does.
- Job worker threads and run-migrations stay environment variables.

## Proof

- Eleven bugs reinstated one at a time, each turning a test red: a bare number taken as seconds,
  a negative fraction's sign lost, `local-event-handler` / `handling.enabled` / namespace / `jobs`
  ignored, an unknown key accepted, the worker dropping the jobs config, a REST app ignoring the
  file's handling switch, the MySQL file losing its driver setting, the sharded file swapping urls.
- The job worker's first test missed one of them - the shipped `PT10S` equals db-scheduler's
  default - so it now builds from a copy with `PT7S`.
- Handling switched off in the PG example's file: the handler test went red. The same in the
  saga's file: events fanned out, the delivery never ran, the saga stalled after step one.
- Key spellings: the MySQL file with keys rewritten kebab, camel and snake loads the same.
