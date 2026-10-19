# wallet-service-poetry-async-sqlite - the project builds with Poetry. Written by
# scripts/sync-examples.py, from its uv twin; edit the template there.
#
#     make            generate what is out of date, then test the built wheel, installed
#     make run        run it
#     make clean      throw away what is generated and the environment

PACKAGE := wallet_service_async_sqlite
SRC     := src/$(PACKAGE)

.PHONY: all env test run schema builders clean
.DEFAULT_GOAL := all

all: test

env:
	poetry install --quiet

# The tests run against the project as its wheel, installed - not the source tree - so a
# file the build leaves out fails here rather than on a server.
test: env
	rm -rf dist && poetry build --quiet --format wheel
	poetry run pip install -q --no-deps --force-reinstall dist/*.whl
	poetry run pytest --pyargs $(PACKAGE)

run: env
	poetry install --quiet
	poetry run python -m $(PACKAGE)

clean:
	rm -rf dist .venv
