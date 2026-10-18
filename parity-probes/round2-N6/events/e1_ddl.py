"""N6-C e1: what DDL outbox_table / notifications_table actually emit, against the docs' DDL blocks."""
from sqlalchemy import MetaData
from ekbatan.schema import outbox_table, notifications_table
from ekbatan.migrations import emit_ddl

for dialect in ("postgresql", "mysql", "mariadb"):
    md = MetaData()
    outbox_table(md)
    notifications_table(md)
    ddl = emit_ddl(md, dialect)
    text = ddl if isinstance(ddl, str) else "\n".join(map(str, ddl))
    print(f"PROBE ddl.{dialect}.has_index: {'CREATE INDEX' in text.upper()}")
    print(f"PROBE ddl.{dialect}.has_unique_event_handler: {'uq_event_notifications_event_handler' in text}")
    print(f"=== {dialect}\n{text}")
