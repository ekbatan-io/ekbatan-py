"""The tables - and note there is no shard column anywhere.

That is the point of packing the coordinate into the id: nothing in the schema knows about
sharding, every database holds the same tables, and a row moved between them is still the same
row. A tenant column would have to be read before it could route, which is the thing this
design avoids.
"""

from __future__ import annotations

from sqlalchemy import CHAR, BigInteger, Column, MetaData, Numeric, String, Table

from ekbatan.schema import outbox_table, timestamp_column, uuid_column

metadata = MetaData()

WALLETS = Table(
    "wallets",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("owner_id", uuid_column(), nullable=False),
    Column("currency", CHAR(3), nullable=False),
    Column("balance", Numeric(19, 4), nullable=False),
    Column("created_date", timestamp_column(), nullable=False),
    Column("updated_date", timestamp_column(), nullable=False),
)

#: Every shard gets its own outbox. A consumer reads all of them, or one connector per
#: database reads each - which is also how the layout stays independently deployable.
EVENTS = outbox_table(metadata)

__all__ = ["EVENTS", "WALLETS", "metadata"]
