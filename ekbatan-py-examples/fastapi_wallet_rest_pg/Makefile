# The build, such as Python has one.
#
# Java's `./gradlew build` starts a container, runs Flyway into it, points jOOQ at the result and
# compiles the generated classes - so a developer never runs codegen and never has a stale
# schema. Python has no step that runs before your code, so Make supplies the one thing that
# makes the difference: a dependency graph with an up-to-date check.
#
#     make run       generate if a migration changed, then serve
#     make test      generate if a migration changed, then test
#     make schema    just the generation
#     make clean     throw the generated module away
#
# `schema.py` is generated and **gitignored**, exactly as Java's `build/generated-jooq` is. A
# fresh clone runs `make test` and gets a schema, because the rule below builds it first -
# which is the same reason a fresh clone of the Java project runs `./gradlew build`.

UV      := uv run
PYTHON  := $(UV) python
MIGRATIONS := $(wildcard migrations/versions/*.py)
PACKAGE := fastapi_wallet_rest_pg

.PHONY: run test schema clean
.DEFAULT_GOAL := test

# The rule that does the work. Newer migrations mean a stale schema, so Make regenerates it -
# and skips the container entirely when nothing has changed, which is what makes this bearable
# to have in front of every other target.
schema.py: $(MIGRATIONS)
	cd ../.. && $(PYTHON) -m ekbatan.schemagen \
		--migrations ekbatan-py-examples/$(PACKAGE)/migrations \
		--out ekbatan-py-examples/$(PACKAGE)/schema.py

schema: schema.py

run: schema.py
	cd .. && $(UV) python -m $(PACKAGE)

test: schema.py
	cd ../.. && $(UV) pytest ekbatan-py-examples/$(PACKAGE)

clean:
	rm -f schema.py
