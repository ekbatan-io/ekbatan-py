# Ledger: the Dracula sweep

Asked on 2026-10-12. A **Dracula** is anything in ekbatan-py that would surprise a developer who
knows the Java port - not necessarily a bug; ordinary Python practice counts if a Java developer
would not expect it. For each one: what it is, why it exists (Python forces it, or a choice),
and whether it could match Java. The user decides on each.

Known before the sweep: database urls that name the driver (`postgresql+psycopg://`).

## Areas

| # | Area | Files | State |
|---|---|---|---|
| A | Domain and persistence | `domain.py`, `ids.py`, `repository.py`, `persistence.py`, `fetching.py`, `schema.py`, `sharding.py`, `clock.py`, `errors.py`, `accounting.py`, `_json.py` | done |
| B | Actions, events, streaming, testing helpers | `action/`, `events/`, `streaming.py`, `testing.py` | done |
| C | Config, migrations, generators | `config.py`, `config_files.py`, `_credentials.py`, `_redact.py`, `migrations.py`, `_yoyo.py`, `schemagen.py`, `codegen.py`, `_tracing.py` | done |
| D | Jobs, locks, the db-scheduler port | `distributedjobs/`, `locks/`, `db-scheduler/` | done |
| E | Using it: packaging, setup, examples, tooling | `pyproject.toml`, extras, `Makefile`, `ekbatan-py-examples/`, `docs/getting-started.md` | done |
| F | Verify a sample of findings by running or reading both sides; merge, dedupe, rank | - | done - 122 findings merged into 9 bugs and 96 Draculas; six re-checked |
| G | Report to the user | - | done |

Findings per area: `docs/dracula/<area>.md`. The merged list: `docs/DRACULAS.md`.
