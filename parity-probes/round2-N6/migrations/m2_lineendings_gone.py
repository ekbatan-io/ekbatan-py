"""N6 / migrations: a changed line ending or a BOM is not an edit; a gone middle file is refused."""
import tempfile, sqlite3
from pathlib import Path
from ekbatan.config import ShardingConfig
from ekbatan.migrations import migrate

def probe(k, v): print(f"PROBE {k}: {v}", flush=True)
def err(fn):
    try: fn(); return "accepted"
    except Exception as e: return f"{type(e).__name__}: {str(e).splitlines()[1] if len(str(e).splitlines())>1 else e}"[:300]

root = Path(tempfile.mkdtemp()); d = root / "m"; d.mkdir(); db = root / "x.db"
L = lambda: ShardingConfig.single(f"sqlite:///{db}")
(d / "V1__a.sql").write_bytes(b"CREATE TABLE a (x int);\nCREATE TABLE a2 (x int);\n")
(d / "V2__b.sql").write_bytes(b"CREATE TABLE b (x int);\n")
(d / "V3__c.sql").write_bytes(b"CREATE TABLE c (x int);\n")
migrate(L(), d)
(d / "V1__a.sql").write_bytes(b"CREATE TABLE a (x int);\r\nCREATE TABLE a2 (x int);\r\n")
probe("crlf_same_lines", err(lambda: migrate(L(), d)))
(d / "V1__a.sql").write_bytes(b"\xef\xbb\xbfCREATE TABLE a (x int);\nCREATE TABLE a2 (x int);\n")
probe("bom", err(lambda: migrate(L(), d)))
(d / "V1__a.sql").write_bytes(b"CREATE TABLE a (x int);\nCREATE TABLE a2 (x int);")
probe("trailing_newline_dropped", err(lambda: migrate(L(), d)))
(d / "V1__a.sql").write_bytes(b"CREATE TABLE a (x int);\nCREATE TABLE a2 (x int);\n")
(d / "V2__b.sql").unlink()
probe("middle_file_gone", err(lambda: migrate(L(), d)))
