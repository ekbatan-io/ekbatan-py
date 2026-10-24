# fastapi-wallet-rest-uv-async-sharded-pg - the project builds with uv. Written by
# scripts/sync-examples.py, from its uv twin; edit the template there.
#
#     make            generate what is out of date, then test the built wheel, installed
#     make run        start the database (compose.yaml), run it, stop the database
#     make schema     regenerate schema.py from the migrations
#     make builders   regenerate builders.py after a model change
#     make clean      throw away what is generated and the environment

PACKAGE := fastapi_wallet_rest_async_sharded_pg
SRC     := src/$(PACKAGE)

.PHONY: all env test run schema builders clean
.DEFAULT_GOAL := all

all: test

env:
	uv sync --quiet

# A container starts when a migration or the settings changed, and not otherwise.
# The settings - where, which image, forced types - are [tool.ekbatan.schemagen].
$(SRC)/schema.py: $(wildcard $(SRC)/migrations/*.sql) pyproject.toml | env
	uv run python -m ekbatan.schemagen

schema: $(SRC)/schema.py

builders: env
	uv run python -m ekbatan.codegen $(PACKAGE).model --out $(SRC)/builders.py

# The tests run against the project as its wheel, installed - not the source tree - so a
# file the build leaves out fails here rather than on a server.
test: $(SRC)/schema.py
	uv sync --no-editable --reinstall-package fastapi-wallet-rest-async-sharded-pg \
		--reinstall-package ekbatan --reinstall-package ekbatan-py-db-scheduler --quiet
	uv run --no-sync pytest --pyargs $(PACKAGE)

run: $(SRC)/schema.py
	uv sync --quiet
	docker compose up -d --wait
	trap 'docker compose stop' EXIT; trap 'exit 130' INT TERM; uv run python -m $(PACKAGE)

clean:
	rm -rf $(SRC)/schema.py dist .venv
