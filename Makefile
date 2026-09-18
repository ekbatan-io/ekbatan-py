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
#     make website    build the site into website/dist
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

.PHONY: all lint format test test-all examples schema build clean help website website-dev
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
# also the place a reader looks to find out what is here.
GENERATES := \
	fastapi_wallet_rest_pg \
	fastapi_wallet_rest_mysql \
	fastapi_wallet_rest_mariadb \
	fastapi_wallet_rest_sharded_pg \
	fastapi_wallet_saga_pg \
	job_worker_pg

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
OURS := src tests ekbatan-py-examples scripts

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
	# The website makes claims about this API in code a reader will copy. Checked here
	# rather than in `npm run check`, which is the Node half and has no interpreter to ask.
	$(UV) python website/scripts/check-api.py

format:
	$(UV) ruff format $(OURS)
	$(UV) ruff check --fix $(OURS)

test:
	$(UV) pytest tests/unit
	$(UV) pytest --dialects=sqlite

test-all:
	$(UV) pytest --dialects=all

examples: schema
	$(UV) pytest ekbatan-py-examples
	cd ekbatan-py-examples && $(UV) python -m wallet_service_sqlite

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
	$(foreach e,$(GENERATES),$(MAKE) -C ekbatan-py-examples/$(e) clean;)

help:
	@grep -E '^#     make' $(MAKEFILE_LIST) | sed 's/^#     //'
