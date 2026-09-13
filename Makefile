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
#     make examples   every example's own test (needs Docker)
#     make schema     regenerate what codegen owns
#     make lint       ruff and mypy
#     make format     rewrite what ruff can
#     make build      the wheel and sdist
#     make clean      remove generated output

UV := uv run

# What is generated, and what it is generated from. Adding a project here is one line.
GENERATED := \
	ekbatan-py-examples/fastapi_wallet_rest_pg/schema.py \
	ekbatan-py-examples/fastapi_wallet_rest_mysql/schema.py \
	ekbatan-py-examples/fastapi_wallet_rest_mariadb/schema.py \
	ekbatan-py-examples/fastapi_wallet_rest_sharded_pg/schema.py \
	ekbatan-py-examples/fastapi_wallet_saga_pg/schema.py \
	ekbatan-py-examples/job_worker_pg/schema.py

.PHONY: all lint format test test-all examples schema build clean help
.DEFAULT_GOAL := all

all: lint test

# -- code generation ---------------------------------------------------------------------------
#
# `schema.py` is generated and gitignored, exactly as Java's `build/generated-jooq` is. The rule
# starts a container, runs the migrations into it and reflects the result - so it is slow, and
# the dependency on the migration files is what stops it running when nothing has changed.

ekbatan-py-examples/fastapi_wallet_rest_pg/schema.py: \
		$(wildcard ekbatan-py-examples/fastapi_wallet_rest_pg/migrations/versions/*.py)
	$(UV) python -m ekbatan.schemagen \
		--migrations ekbatan-py-examples/fastapi_wallet_rest_pg/migrations \
		--out $@

ekbatan-py-examples/fastapi_wallet_rest_mysql/schema.py: \
		$(wildcard ekbatan-py-examples/fastapi_wallet_rest_mysql/migrations/versions/*.py)
	$(UV) python -m ekbatan.schemagen \
		--migrations ekbatan-py-examples/fastapi_wallet_rest_mysql/migrations \
		--out $@ \
		--image mysql:8.4

ekbatan-py-examples/fastapi_wallet_rest_mariadb/schema.py: \
		$(wildcard ekbatan-py-examples/fastapi_wallet_rest_mariadb/migrations/versions/*.py)
	$(UV) python -m ekbatan.schemagen \
		--migrations ekbatan-py-examples/fastapi_wallet_rest_mariadb/migrations \
		--out $@ \
		--image mariadb:11.4

ekbatan-py-examples/fastapi_wallet_rest_sharded_pg/schema.py: \
		$(wildcard ekbatan-py-examples/fastapi_wallet_rest_sharded_pg/migrations/versions/*.py)
	$(UV) python -m ekbatan.schemagen \
		--migrations ekbatan-py-examples/fastapi_wallet_rest_sharded_pg/migrations \
		--out $@

ekbatan-py-examples/fastapi_wallet_saga_pg/schema.py: \
		$(wildcard ekbatan-py-examples/fastapi_wallet_saga_pg/migrations/versions/*.py)
	$(UV) python -m ekbatan.schemagen \
		--migrations ekbatan-py-examples/fastapi_wallet_saga_pg/migrations \
		--out $@

ekbatan-py-examples/job_worker_pg/schema.py: \
		$(wildcard ekbatan-py-examples/job_worker_pg/migrations/versions/*.py)
	$(UV) python -m ekbatan.schemagen \
		--migrations ekbatan-py-examples/job_worker_pg/migrations \
		--out $@

schema: $(GENERATED)

# -- checks ------------------------------------------------------------------------------------
#
# `lint` depends on the generated modules because mypy reads them: a fresh clone would otherwise
# report "cannot find implementation for module ...schema", which is true and unhelpful. Java's
# compiler has the same dependency and its build resolves it the same way.

# `db-scheduler/` is a separate distribution with its own workflow and its own ruff config, so
# it lints itself. Naming the directories here rather than passing `.` is what keeps the two
# from both half-owning it - and is why this target does not fail on formatting that belongs to
# a package this one only vendors.
OURS := src tests ekbatan-py-examples scripts

lint: $(GENERATED)
	$(UV) ruff check $(OURS)
	$(UV) ruff format --check $(OURS)
	$(UV) mypy

format:
	$(UV) ruff format $(OURS)
	$(UV) ruff check --fix $(OURS)

test:
	$(UV) pytest tests/unit
	$(UV) pytest --dialects=sqlite

test-all:
	$(UV) pytest --dialects=all

examples: $(GENERATED)
	$(UV) pytest ekbatan-py-examples
	cd ekbatan-py-examples && $(UV) python -m wallet_service_sqlite

build:
	uv build

clean:
	rm -f $(GENERATED)

help:
	@grep -E '^#     make' $(MAKEFILE_LIST) | sed 's/^#     //'
