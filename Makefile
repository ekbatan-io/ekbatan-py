# Every command this repository has, in one place.
#
# uv does the work in every recipe - this file exists because uv has no task runner, and the
# alternative is commands living in docstrings where nobody finds them. It is also what gives
# code generation the up-to-date check Gradle gives the Java port: a generated module depends on
# the migrations it came from, so it is rebuilt when they change and skipped when they have not.
#
#     make            lint, type-check and the fast tests
#     make test       the suite on SQLite
#     make test-all   the suite on PostgreSQL, MySQL and MariaDB too (needs Docker)
#     make test-psycopg-async  the PostgreSQL suite with its async runs on psycopg's async mode
#     make examples   every example's own test (needs Docker)
#     make schema     regenerate what codegen owns
#     make lint       ruff and mypy
#     make format     rewrite what ruff can
#     make website    build the site into website/dist
#     make build      the wheel and sdist
#     make clean      remove generated output

UV := uv run

# The examples: fourteen, each a project of its own and each built three ways - with uv, pip and
# Poetry - as Java carries each of its examples per build tool. `scripts/sync-examples.py` writes
# the pip and Poetry projects from the uv one and keeps them in step.
EXAMPLES := \
	fastapi-wallet-rest-uv-pg fastapi-wallet-rest-pip-pg fastapi-wallet-rest-poetry-pg \
	fastapi-wallet-rest-uv-mysql fastapi-wallet-rest-pip-mysql fastapi-wallet-rest-poetry-mysql \
	fastapi-wallet-rest-uv-mariadb fastapi-wallet-rest-pip-mariadb fastapi-wallet-rest-poetry-mariadb \
	fastapi-wallet-rest-uv-sharded-pg fastapi-wallet-rest-pip-sharded-pg fastapi-wallet-rest-poetry-sharded-pg \
	fastapi-wallet-rest-uv-async-sharded-pg fastapi-wallet-rest-pip-async-sharded-pg fastapi-wallet-rest-poetry-async-sharded-pg \
	fastapi-wallet-rest-uv-async-pg fastapi-wallet-rest-pip-async-pg fastapi-wallet-rest-poetry-async-pg \
	fastapi-wallet-rest-uv-async-mysql fastapi-wallet-rest-pip-async-mysql fastapi-wallet-rest-poetry-async-mysql \
	fastapi-wallet-rest-uv-async-mariadb fastapi-wallet-rest-pip-async-mariadb fastapi-wallet-rest-poetry-async-mariadb \
	fastapi-wallet-saga-uv-pg fastapi-wallet-saga-pip-pg fastapi-wallet-saga-poetry-pg \
	fastapi-wallet-saga-uv-async-pg fastapi-wallet-saga-pip-async-pg fastapi-wallet-saga-poetry-async-pg \
	job-worker-uv-pg job-worker-pip-pg job-worker-poetry-pg \
	job-worker-uv-async-pg job-worker-pip-async-pg job-worker-poetry-async-pg \
	wallet-service-uv-sqlite wallet-service-pip-sqlite wallet-service-poetry-sqlite \
	wallet-service-uv-async-sqlite wallet-service-pip-async-sqlite wallet-service-poetry-async-sqlite

.PHONY: all lint format test test-all test-psycopg-async test-gevent examples schema build clean help website website-dev
.DEFAULT_GOAL := all

all: lint test

# -- code generation ---------------------------------------------------------------------------
#
# Each example carries its own Makefile, as each Java example carries its own wrapper - so this
# delegates rather than repeating the rules. `$(MAKE) -C` also means the up-to-date check is the
# example's own: it starts a container when its migrations changed and does nothing when they
# have not.

# Listed rather than globbed: `$(wildcard */)` does not filter to directories on the make that
# ships with macOS (3.81), so it picked up README.md and asked for a schema from it. A list is
# also the place a reader looks to find out what is here. These are the uv projects whose
# `schema.py` is generated - the ones `lint` type-checks; their pip and Poetry twins hold the
# same code.
GENERATES := \
	fastapi-wallet-rest-uv-pg \
	fastapi-wallet-rest-uv-mysql \
	fastapi-wallet-rest-uv-mariadb \
	fastapi-wallet-rest-uv-sharded-pg \
	fastapi-wallet-rest-uv-async-sharded-pg \
	fastapi-wallet-rest-uv-async-pg \
	fastapi-wallet-rest-uv-async-mysql \
	fastapi-wallet-rest-uv-async-mariadb \
	fastapi-wallet-saga-uv-pg \
	fastapi-wallet-saga-uv-async-pg \
	job-worker-uv-pg \
	job-worker-uv-async-pg

.PHONY: $(addprefix schema-,$(GENERATES))
$(addprefix schema-,$(GENERATES)): schema-%:
	$(MAKE) -C ekbatan-py-examples/$* schema

schema: $(addprefix schema-,$(GENERATES))

# -- checks ------------------------------------------------------------------------------------
#
# `lint` depends on the generated modules because mypy reads them: a fresh clone would otherwise
# report "cannot find implementation for module ...schema", which is true and unhelpful. Java's
# compiler has the same dependency and its build resolves it the same way.

# `db-scheduler/` is a separate distribution with its own workflow and its own ruff config, so
# it lints itself. Naming the directories here rather than passing `.` is what keeps the two
# from both half-owning it - and is why this target does not fail on formatting that belongs to
# a package this one only vendors.
OURS := src tests ekbatan-py-examples scripts website/src/setup-guide

lint: schema
	$(UV) ruff check $(OURS)
	$(UV) ruff format --check $(OURS)
	$(UV) mypy
	# A docs tree is written page by page and cross-linked to pages that do not exist yet.
	# That is fine while it is being written and a broken promise once it ships, so it is
	# checked rather than remembered.
	$(UV) python scripts/links.py
	# Every divergence from the Java port must say whether anybody chose it. A judgement
	# call filed as a neutral difference is how one went unreviewed for weeks.
	$(UV) python scripts/divergence.py
	# Java's annotation processor runs on every compile, so a generated builder there cannot
	# be stale or missing. Here the file is committed, so this restores the guarantee.
	$(UV) python scripts/check-builders.py
	# Each example is carried three times, one per build tool; the copies must stay the code
	# of the one they were written from.
	$(UV) python scripts/sync-examples.py --check
	# Both trees make claims about this API in code a reader will copy: what they import,
	# what they call on a class, and whether a snippet parses at all. Checked here rather
	# than in `npm run check`, which is the Node half and has no interpreter to ask.
	$(UV) python scripts/check-api.py
	# `docs/` is the canonical Markdown and the website renders the same knowledge. Prose
	# written into one and never carried to the other is how the two stopped agreeing once.
	$(UV) python scripts/docs-website-sync.py
	# And the docs tree against the Java port's. Not fatal - the Java docs are only there if
	# the sibling checkout is - so this reports and does not fail the build.
	-$(UV) python scripts/docs-parity.py --sections 2>/dev/null | tail -1

format:
	$(UV) ruff format $(OURS)
	$(UV) ruff check --fix $(OURS)

test:
	$(UV) pytest tests/unit
	$(UV) pytest --dialects=sqlite

test-all:
	$(UV) pytest --dialects=all

# ekbatan.aio's runs on PostgreSQL connect through asyncpg, the async default. This runs the whole
# PostgreSQL suite again with them on psycopg's own async mode - what a url naming
# `postgresql+psycopg_async://` connects with - so that mode is proven by the same scenarios.
test-psycopg-async:
	$(UV) pytest tests/integration --dialects=postgres --async-driver=psycopg_async

# The framework is synchronous, so under gevent it is concurrent for free - every blocking call
# yields, not only the ones a library knows about, which is the one thing the asyncio answer
# cannot give. It needs no code of ours, and this is what keeps that true.
#
# The whole suite, nothing excluded. An earlier version of this target skipped `schemagen` on
# the belief that testcontainers disliked monkey-patching; it did not - Docker was not running
# on the machine where that was concluded, and with it up the container tests pass patched too.
test-gevent:
	$(UV) python -m gevent.monkey --module pytest

# Every example, each in its own environment, each tested as its built wheel installed - and the
# SQLite one run end to end. The pip projects make their venv with a Python found by uv, since
# the system `python3` may be older than 3.13; the Poetry ones need `poetry` on the path.
EXAMPLE_PYTHON := $(shell uv python find '>=3.13' 2>/dev/null)

examples:
	@set -e; for e in $(EXAMPLES); do \
		echo "== $$e"; $(MAKE) --no-print-directory -C ekbatan-py-examples/$$e PYTHON=$(EXAMPLE_PYTHON); \
	done
	$(MAKE) --no-print-directory -C ekbatan-py-examples/wallet-service-uv-sqlite run
	$(MAKE) --no-print-directory -C ekbatan-py-examples/wallet-service-uv-async-sqlite run

# -- the website -------------------------------------------------------------------------------
#
# The one part of this repository that does not run through uv. Node is confined to website/
# and to its own workflow; these targets exist so `make help` lists them rather than leaving
# somebody to find npm scripts on their own.

website:
	cd website && npm install && npm run build

website-dev:
	cd website && npm install && npm run dev

build:
	uv build

clean:
	$(foreach e,$(EXAMPLES),$(MAKE) -C ekbatan-py-examples/$(e) clean;)

help:
	@grep -E '^#     make' $(MAKEFILE_LIST) | sed 's/^#     //'
