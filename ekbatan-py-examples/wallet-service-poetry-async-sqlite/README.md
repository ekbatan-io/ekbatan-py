# wallet-service-poetry-async-sqlite

A wallet, end to end, on **SQLite**, on native asyncio: the HTTP examples' model, actions, event handler
and repositories, run by a script instead of a web server. Two shards, `global` and `mexico`, each
a SQLite file of its own. It has no Java twin - Java has no SQLite example - and is built three
ways, with uv, pip and Poetry.

<!-- build: written by scripts/sync-examples.py -->
```
make run        # run it
make            # test the built wheel, installed
```

Built with **Poetry**. The same project with [uv](../wallet-service-uv-async-sqlite) and [pip and setuptools](../wallet-service-pip-async-sqlite) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

Needs `poetry` 2 on the path.
<!-- /build -->

## What it does

`make run` runs `__main__.py`, which creates a wallet on each shard, deposits into one, waits for
the in-process handler to write its notification, closes a wallet twice - the second time changes
nothing and emits nothing - and prints the outbox: every row a consumer of the event stream would
read, each written in the same transaction as the change it describes.

## What to look at, in order

- **`model.py`**, **`actions.py`**, **`handlers.py`**, **`repository.py`** - the HTTP examples'
  own files.
- **`schema.py`** - written by hand here, so the example reads in one place. In a real project it
  is generated: `python -m ekbatan.schemagen` starts a container, runs the migrations and reflects
  the result. The HTTP examples do it that way.
- **`app.py`** - the wiring. The tables come from `create_all`, which is for development only.

## What it does not show

No HTTP, no keyed lock (SQLite has none), no migrations from files - see the HTTP examples for
those.

## Its test

```
make
```

Each wallet lands on the shard its country names, a deposit's event reaches the handler and
leaves a notification, closing a wallet closes it, and the script runs.
