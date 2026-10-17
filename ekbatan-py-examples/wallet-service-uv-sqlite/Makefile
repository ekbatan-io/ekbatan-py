# wallet-service-uv-sqlite - the project builds with uv. Written by
# scripts/sync-examples.py, from its uv twin; edit the template there.
#
#     make            generate what is out of date, then test the built wheel, installed
#     make run        run it
#     make clean      throw away what is generated and the environment

PACKAGE := wallet_service_sqlite
SRC     := src/$(PACKAGE)

.PHONY: all env test run schema builders clean
.DEFAULT_GOAL := all

all: test

env:
	uv sync --quiet

# The tests run against the project as its wheel, installed - not the source tree - so a
# file the build leaves out fails here rather than on a server.
test: env
	uv sync --no-editable --reinstall-package wallet-service-sqlite --quiet
	uv run --no-sync pytest --pyargs $(PACKAGE)

run: env
	uv sync --quiet
	uv run python -m $(PACKAGE)

clean:
	rm -rf dist .venv
