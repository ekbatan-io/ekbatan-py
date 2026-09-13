# fastapi_wallet_rest_pg
#
# Java's examples each carry their own wrapper, so you can clone the repository, `cd` into one
# and build it without the rest. This is that: every target here runs from this directory, and
# `uv` walks up to find the project on its own.
#
#     make            generate if a migration changed, then test
#     make run        generate if a migration changed, then serve
#     make schema     just the generation
#     make clean      throw the generated module away
#
# `schema.py` is generated and **gitignored**, exactly as Java's `build/generated-jooq` is. A
# fresh clone has none, runs `make`, and gets one - the same reason a fresh clone of the Java
# project runs `./gradlew build`.

PACKAGE := fastapi_wallet_rest_pg
IMAGE   := postgres:17-alpine

.PHONY: all run schema test clean
.DEFAULT_GOAL := all

all: test

# The dependency on the migration files is what makes this bearable to have in front of every
# other target: it starts a container when they change and does nothing at all when they have
# not.
schema.py: $(wildcard migrations/versions/*.py)
	uv run python -m ekbatan.schemagen --migrations migrations --out schema.py --image $(IMAGE)

schema: schema.py

run: schema.py
	cd .. && uv run python -m $(PACKAGE)

test: schema.py
	cd ../.. && uv run pytest ekbatan-py-examples/$(PACKAGE)

clean:
	rm -f schema.py
