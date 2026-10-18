"""docs/database/multi-database.md:22-26 and the dialect pages: what each column helper renders as."""
from sqlalchemy.dialects import postgresql, mysql, sqlite
from sqlalchemy.dialects.mysql import base as mysqlbase
from ekbatan.schema import json_column, timestamp_column, uuid_column
from sqlalchemy import MetaData, Table
from sqlalchemy.schema import CreateTable
md = MetaData()
from sqlalchemy import Column
t = Table("t", md, Column("u", uuid_column()), Column("ts", timestamp_column()), Column("j", json_column()))
for name, d in [("postgresql", postgresql.dialect()), ("mysql", mysql.dialect()), ("mariadb", mysql.dialect(is_mariadb=True)), ("sqlite", sqlite.dialect())]:
    try:
        d.is_mariadb = name == "mariadb"
    except Exception:
        pass
    ddl = str(CreateTable(t).compile(dialect=d)).replace("\n", " ").replace("\t", "")
    print(f"PROBE {name}: {ddl}")
