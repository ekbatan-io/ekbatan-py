"""Alembic's entry point, driven as a library.

There is no `alembic.ini`. `ekbatan.migrations.migrate` builds a `Config` in memory and sets
`sqlalchemy.url` on it, one per shard, so the connection settings the application already has
are the ones the migration uses - rather than a second copy in a file to keep right.

`target_metadata` stays `None` because these revisions are written by hand. Point it at
`schema.metadata` if you want `alembic revision --autogenerate` to diff against it.
"""

from alembic import context
from sqlalchemy import create_engine

url = context.config.get_main_option("sqlalchemy.url")
assert url is not None, "ekbatan.migrations sets this on the Config it builds"
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
