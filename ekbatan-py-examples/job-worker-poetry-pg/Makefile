# job-worker-poetry-pg - the project builds with Poetry. Written by
# scripts/sync-examples.py, from its uv twin; edit the template there.
#
#     make            generate what is out of date, then test the built wheel, installed
#     make run        run it (needs the database in README.md)
#     make schema     regenerate schema.py from the migrations
#     make builders   regenerate builders.py after a model change
#     make clean      throw away what is generated and the environment

PACKAGE := job_worker_pg
SRC     := src/$(PACKAGE)
IMAGE   := postgres:17-alpine

.PHONY: all env test run schema builders clean
.DEFAULT_GOAL := all

all: test

env:
	poetry install --quiet

# A container starts when a migration changed, and not otherwise.
$(SRC)/schema.py: $(wildcard $(SRC)/migrations/*.sql) | env
	poetry run python -m ekbatan.schemagen --migrations $(SRC)/migrations --out $@ --image $(IMAGE)

schema: $(SRC)/schema.py

builders: env
	poetry run python -m ekbatan.codegen $(PACKAGE).model --out $(SRC)/builders.py

# The tests run against the project as its wheel, installed - not the source tree - so a
# file the build leaves out fails here rather than on a server.
test: $(SRC)/schema.py
	rm -rf dist && poetry build --quiet --format wheel
	poetry run pip install -q --no-deps --force-reinstall dist/*.whl
	poetry run pytest --pyargs $(PACKAGE)

run: $(SRC)/schema.py
	poetry install --quiet
	poetry run python -m $(PACKAGE)

clean:
	rm -rf $(SRC)/schema.py dist .venv
