# Ledger: every example as a standalone project, in uv, pip and Poetry

Asked on 2026-10-17: more examples, one per build system, as Java has Gradle and Maven versions of
its examples; and the docs and website following the chosen build system. **Scope chosen by the
user: the full matrix** - every example in all three, 7 x 3 = 21 projects. (Java has both build
tools for its REST examples only; this goes one step further.)

## Decisions (built, not asked)

- **Names follow Java's** `<stack>-<app>-<build>-<variant>-<db>`:
  `fastapi-wallet-rest-uv-pg`, `fastapi-wallet-rest-pip-mysql`,
  `fastapi-wallet-rest-poetry-sharded-pg`, `fastapi-wallet-saga-uv-pg`, `job-worker-pip-pg`,
  `wallet-service-poetry-sqlite`.
- **Each is a standalone project**: its own `pyproject.toml`, `Makefile`, `README.md` and
  `.gitignore`, the package under `src/`, its own environment. `cd` into one and `make` builds,
  generates and tests it, as `cd` into a Java example and `./gradlew build` does.
- **The package keeps its name** (`fastapi_wallet_rest_pg`) in all three build versions, so the
  code is byte-identical across them; only the build files differ. A test keeps it so.
- **Where `ekbatan` comes from:** it is not on PyPI yet, so each project takes it from this
  repository - uv through `[tool.uv.sources]`, Poetry through a path dependency, pip through an
  editable install in its Makefile. One line each, to delete once it is published.
- **The tests run against the built wheel, installed** - the check that would have caught bug 7.
- **Lock files are not committed**: they would pin a path into this checkout.

## Steps

| # | Step | State |
|---|---|---|
| 1 | Inventory: every file, config, CI job, script and doc that knows the example paths | done - about 40 files |
| 2 | Build one project (`fastapi-wallet-rest-uv-pg`) by hand; run it standalone, from its wheel | done |
| 3 | The pip and Poetry versions of it | done |
| 4 | Generate the other 18 from the same pattern; each runs | done - `scripts/sync-examples.py`; 21 of 21 pass |
| 5 | Root: Makefile, mypy/ruff config, `check-builders.py`, structure tests, CI | done |
| 6 | Sync tests: identical code across build versions; the REST trio's shared files | done - `sync-examples.py --check` in `make lint`; `test_setup_guide.py` |
| 7 | Docs, website, READMEs, agent skills: every path, and the setup guide per build system | done |
| 8 | Full run: all 21, lint, suite, site | done - 21 of 21, lint, 1,256 tests, site; setup guide 9 of 9 |

## Findings

- **Poetry 2 applies `[tool.poetry.dependencies]` only to packages listed in `[project]`
  dependencies.** The scheduler, a dependency of `ekbatan`, had to be listed too, or Poetry looked
  for it on PyPI and failed on its pre-release version.
- `codegen` in Poetry's dev group, not the runtime list: the built wheel's metadata shows only
  the runtime extras.
- The uv projects install `ekbatan` itself as a wheel too (`uv sync --no-editable`), so their
  tests also check that ekbatan's own package is complete. **Found later (EKB-0260):** uv reuses
  that wheel until ekbatan's version changes, so after the first run the uv projects tested a
  stale ekbatan. Their test step now reinstalls ekbatan and the scheduler every time.
- A setuptools build leaves `src/*.egg-info/` behind; the sync check skips it, as git does.
- `make examples`: **21 of 21 pass** (REST 11 each, sharded 18, saga 14, job worker 9,
  SQLite 19), each in its own environment, each as its wheel installed; the SQLite script runs.
- Planted fault: a hand edit in a Poetry twin - `sync-examples.py --check` names the file.
