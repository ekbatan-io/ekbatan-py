"""Shared setup: the sqlite example service on a temp file, plus a probe printer."""
import sys, tempfile, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ekbatan-py-examples/wallet-service-uv-sqlite/src"))

def probe(key, fn):
    try:
        v = fn()
        print(f"PROBE {key}: {v!r}"[:500])
    except Exception as e:
        msg = (str(e).splitlines() or [""])[0][:300]
        print(f"PROBE {key}: raised {type(e).__name__}: {msg}")

def service(**kw):
    from wallet_service_sqlite.app import build
    d = tempfile.mkdtemp()
    return build({"WALLET_DATABASE_URL": f"sqlite:///{d}/w.db"}, **kw)
