# fastapi-wallet-saga-poetry-async-pg - the project builds with Poetry. Written by
# scripts/sync-examples.py, from its uv twin; edit the template there.
#
#     make            generate what is out of date, then test the built wheel, installed
#     make run        start the database (compose.yaml), run it, stop the database
#     make schema     regenerate schema.py from the migrations
#     make builders   regenerate builders.py after a model change
#     make clean      throw away what is generated and the environment

PACKAGE := fastapi_wallet_saga_async_pg
SRC     := src/$(PACKAGE)

.PHONY: all env test run schema builders clean
.DEFAULT_GOAL := all

all: test

env:
	poetry install --quiet

# A container starts when a migration or the settings changed, and not otherwise.
# The settings - where, which image, forced types - are [tool.ekbatan.schemagen].
# PYTHONPATH=src: read the source, not the wheel `make test` installed.
$(SRC)/schema.py: $(wildcard $(SRC)/migrations/*.sql) pyproject.toml | env
	PYTHONPATH=src poetry run python -m ekbatan.schemagen

schema: $(SRC)/schema.py

builders: env
	PYTHONPATH=src poetry run python -m ekbatan.codegen $(PACKAGE).model --out $(SRC)/builders.py

# The tests run against the project as its wheel, installed - not the source tree - so a
# file the build leaves out fails here rather than on a server.
test: $(SRC)/schema.py
	rm -rf dist && poetry build --quiet --format wheel
	poetry run pip install -q --no-deps --force-reinstall dist/*.whl
	poetry run pytest --pyargs $(PACKAGE)

run: $(SRC)/schema.py
	poetry install --quiet
	docker compose up -d --wait
	trap 'docker compose stop' EXIT; trap 'exit 130' INT TERM; poetry run python -m $(PACKAGE)

clean:
	rm -rf $(SRC)/schema.py dist .venv
