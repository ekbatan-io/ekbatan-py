# wallet_service_sqlite
#
# Java's examples each carry their own wrapper, so you can clone the repository, `cd` into one
# and build it without the rest. This is that.
#
#     make        run it
#     make test   its own test
#
# Nothing is generated here. This example runs on SQLite with nothing installed, which is its
# whole job, and generating a schema needs Docker - so `schema.py` is written by hand and
# committed. Every other example generates; see `../README.md`.

PACKAGE := wallet_service_sqlite

.PHONY: all run test
.DEFAULT_GOAL := run

all: run

run:
	cd .. && uv run python -m $(PACKAGE)

test:
	cd ../.. && uv run pytest ekbatan-py-examples/$(PACKAGE)
