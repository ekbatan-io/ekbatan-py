"""Area N2: does each generated schema.py import, and which emitted type names break it.
Reads py-schema/<dialect>/schema.py (written by schemagen from migrations/<dialect>/)."""

import importlib.util
import pathlib
import re

import sqlalchemy

HERE = pathlib.Path(__file__).parent
for dialect in ("pg", "mysql", "mariadb"):
    path = HERE / "py-schema" / dialect / "schema.py"
    spec = importlib.util.spec_from_file_location(f"s_{dialect}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        print(f"PROBE schemagen.{dialect}.imports: OK")
    except Exception as e:  # noqa: BLE001
        print(f"PROBE schemagen.{dialect}.imports: ERR {type(e).__name__}: {e}")
    source = path.read_text()
    names = re.search(r"^from sqlalchemy import (.*)$", source, re.M).group(1).split(", ")
    missing = [n for n in names if not hasattr(sqlalchemy, n)]
    print(f"PROBE schemagen.{dialect}.namesNotInSqlalchemy: {missing}")
    for line in source.splitlines():
        m = re.match(r'\s+Column\("([^"]+)", ([A-Z_]+)\((.*?)\)(,|\))', line)
        if m and hasattr(sqlalchemy, m.group(2)):
            try:
                eval(f"sqlalchemy.{m.group(2)}({m.group(3)})")
            except Exception as e:  # noqa: BLE001
                print(f"PROBE schemagen.{dialect}.column.{m.group(1)}: ERR {type(e).__name__}: {e}")
        elif m:
            print(f"PROBE schemagen.{dialect}.column.{m.group(1)}: ERR emitted {m.group(2)}(...), not importable from sqlalchemy")
