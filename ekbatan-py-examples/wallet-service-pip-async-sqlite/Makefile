# wallet-service-pip-async-sqlite - the project builds with pip and setuptools. Written by
# scripts/sync-examples.py, from its uv twin; edit the template there.
#
#     make            generate what is out of date, then test the built wheel, installed
#     make run        run it
#     make clean      throw away what is generated and the environment
#
# PYTHON must be 3.13 or later: `make PYTHON=python3.13` if `python3` is older.

PACKAGE := wallet_service_async_sqlite
SRC     := src/$(PACKAGE)
PYTHON  ?= python3
VENV    := .venv/bin

.PHONY: all env test run schema builders clean
.DEFAULT_GOAL := all

all: test

# ekbatan is not on PyPI yet: until it is, it - and its scheduler - come from this
# repository. Once it is, `pip install --group dev -e .` alone does this.
$(VENV)/python:
	$(PYTHON) -m venv .venv
	$(VENV)/pip install -q -e ../../db-scheduler -e "../..[sqlite-async]"
	$(VENV)/pip install -q --group dev -e .

env: $(VENV)/python

# The tests run against the project as its wheel, installed - not the source tree - so a
# file the build leaves out fails here rather than on a server.
test: env
	rm -rf dist build && $(VENV)/pip wheel -q --no-deps . -w dist
	$(VENV)/pip install -q --no-deps --force-reinstall dist/*.whl
	$(VENV)/python -m pytest --pyargs $(PACKAGE)

run: env
	$(VENV)/pip install -q -e .
	$(VENV)/python -m $(PACKAGE)

clean:
	rm -rf dist .venv build src/*.egg-info
