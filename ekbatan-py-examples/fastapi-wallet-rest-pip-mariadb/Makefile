# fastapi-wallet-rest-pip-mariadb - the project builds with pip and setuptools. Written by
# scripts/sync-examples.py, from its uv twin; edit the template there.
#
#     make            generate what is out of date, then test the built wheel, installed
#     make run        run it (needs the database in README.md)
#     make schema     regenerate schema.py from the migrations
#     make builders   regenerate builders.py after a model change
#     make clean      throw away what is generated and the environment
#
# PYTHON must be 3.13 or later: `make PYTHON=python3.13` if `python3` is older.

PACKAGE := fastapi_wallet_rest_mariadb
SRC     := src/$(PACKAGE)
IMAGE   := mariadb:11.4
PYTHON  ?= python3
VENV    := .venv/bin

.PHONY: all env test run schema builders clean
.DEFAULT_GOAL := all

all: test

# ekbatan is not on PyPI yet: until it is, it - and its scheduler - come from this
# repository. Once it is, `pip install --group dev -e .` alone does this.
$(VENV)/python:
	$(PYTHON) -m venv .venv
	$(VENV)/pip install -q -e ../../db-scheduler -e "../..[mariadb,codegen]"
	$(VENV)/pip install -q --group dev -e .

env: $(VENV)/python

# A container starts when a migration changed, and not otherwise.
$(SRC)/schema.py: $(wildcard $(SRC)/migrations/*.sql) | env
	$(VENV)/python -m ekbatan.schemagen --migrations $(SRC)/migrations --out $@ --image $(IMAGE)

schema: $(SRC)/schema.py

builders: env
	$(VENV)/python -m ekbatan.codegen $(PACKAGE).model --out $(SRC)/builders.py

# The tests run against the project as its wheel, installed - not the source tree - so a
# file the build leaves out fails here rather than on a server.
test: $(SRC)/schema.py
	rm -rf dist build && $(VENV)/pip wheel -q --no-deps . -w dist
	$(VENV)/pip install -q --no-deps --force-reinstall dist/*.whl
	$(VENV)/python -m pytest --pyargs $(PACKAGE)

run: $(SRC)/schema.py
	$(VENV)/pip install -q -e .
	$(VENV)/python -m $(PACKAGE)

clean:
	rm -rf $(SRC)/schema.py dist .venv build src/*.egg-info
