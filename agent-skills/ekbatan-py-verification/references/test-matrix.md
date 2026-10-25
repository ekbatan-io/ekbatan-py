# Test matrix

## What exists

| | count | needs Docker |
| --- | ---: | :---: |
| `tests/unit/` | 250 | no |
| `tests/integration/`, on one dialect | 322 | for all but SQLite |
| the whole suite on SQLite | 578 | no |
| the whole suite on four dialects | 1,370 | yes |
| the examples' own tests | 78 | yes |

264 of the tests are parametrised over the dialect, which is why `--dialects=all` multiplies.

## Commands

```bash
uv run pytest tests/unit              # no database at all, about a second
make test                             # unit, then everything on SQLite
uv run pytest --dialects=postgres     # one dialect, in a container
make test-all                         # all four
make examples                         # regenerates schemas first
make lint                             # ruff, mypy --strict, and the docs link checker
```

`--dialects` takes `sqlite`, `postgres`, `mysql`, `mariadb` or `all`, comma-separated.

## What proves what

| the change touches | run |
| --- | --- |
| pure logic, a plan, a builder | `tests/unit` |
| any SQL | **`make test-all`.** Not optional |
| timestamps | `make test-all`, and note CI also runs PostgreSQL under a non-UTC process |
| `schemagen` | `pytest tests/unit/test_schemagen_config.py tests/unit/test_schema_converters.py tests/unit/test_schemagen_records.py tests/unit/test_typed_records_mypy.py`, `pytest tests/integration/test_schemagen*.py --dialects=all` (every type on every database; converters; the pgvector test pulls `pgvector/pgvector:pg17` and runs on PostgreSQL only), then `make examples` |
| an example | `make examples` |
| the docs | `make lint` - it checks every relative link |
| the website | `cd website && npm run build && npm run check` |

## Why SQLite is not enough

It is the default because a suite needing a daemon is a suite people stop running. It is **not a
production dialect**:

- no `SKIP LOCKED`, so the scheduler and the SQL keyed lock degrade to single-process behaviour;
- types are advisory, so a mapping bug PostgreSQL rejects loudly can pass here;
- no real write concurrency.

SQLite passing is necessary and not sufficient.

## CI

Three workflows on GitHub (`ekbatan-io/ekbatan-py`), each modelled on its Java twin:

- **CI** (`ci.yml`) on every push and pull request to main, except a push that only touches
  `website/**`: lint and types, the fast suite, the whole suite under gevent, a tracing pass that
  asserts the span names, the three real dialects in a matrix, PostgreSQL with `ekbatan.aio` on
  psycopg's async mode, and PostgreSQL again under `TZ=America/New_York` - which is the job that
  would catch a driver inferring a zoned type. Python is pinned to 3.14.
- **Website** (`deploy-website.yml`) on a push that touches `website/**` or `docs/**`: builds the
  site for `/ekbatan-py/`, runs its checks and the Python ones that read it, and deploys it to
  GitHub Pages.
- **Heavy Verification** (`heavy-verification.yml`) nightly, after midnight UTC, if main changed
  the day before - or by hand, where `force` skips that test: every example, and the setup guide
  followed as written for every database, installer and mode.

The tracing job is the only one that installs the OpenTelemetry SDK. Every other job proves the
framework runs without it.
