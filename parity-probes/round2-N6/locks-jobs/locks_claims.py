"""Run the keyed-lock claims in docs/database/keyed-locks.md against real PG, MySQL, MariaDB, Redis.
Containers: n6b-pg 58720, n6b-mysql 58721, n6b-maria 58722, n6b-redis 58723 (started by hand)."""
import asyncio, threading, time
from datetime import timedelta as td
import redis
from sqlalchemy import create_engine, text
import ekbatan.locks as L
from ekbatan.locks import PostgresKeyedLockProvider, MySQLKeyedLockProvider, MariaDBKeyedLockProvider, lock_key_hash
from ekbatan.locks.redis import RedisKeyedLockProvider
from ekbatan.errors import LockAcquisitionError, ValidationError

PW = "pw"
URLS = {"pg": f"postgresql+psycopg://postgres:{PW}@127.0.0.1:58720/postgres",
        "mysql": f"mysql+pymysql://root:{PW}@127.0.0.1:58721/mysql",
        "mariadb": f"mysql+pymysql://root:{PW}@127.0.0.1:58722/mysql"}
P = lambda k, v: print(f"PROBE {k}: {v}", flush=True)

P("ekbatan.locks exports", sorted(L.__all__))
P("any InProcess provider in ekbatan.locks", any("InProcess" in n or "Local" in n for n in dir(L)))
P("watchdog threads before any hold", [t.name for t in threading.enumerate() if "watch" in t.name.lower() or "deadline" in t.name.lower()])

def other_thread(fn):
    out = {}
    def run():
        t0 = time.monotonic()
        try: out["r"] = fn()
        except Exception as e: out["r"] = e
        out["dt"] = time.monotonic() - t0
    t = threading.Thread(target=run); t.start(); t.join(); return out

providers = {}
for name, cls in [("pg", PostgresKeyedLockProvider), ("mysql", MySQLKeyedLockProvider), ("mariadb", MariaDBKeyedLockProvider)]:
    providers[name] = cls(create_engine(URLS[name], pool_size=5, max_overflow=0))
providers["redis"] = RedisKeyedLockProvider(redis.Redis(host="127.0.0.1", port=58723))

for name, locks in providers.items():
    key = f"n6:{name}:a"
    lease = locks.try_acquire(key, td(milliseconds=100), td(seconds=30))
    P(f"{name} try_acquire free key", lease is not None)
    inner = locks.acquire(key, td(seconds=60))          # re-entry, same thread
    P(f"{name} re-entry same thread returns", inner is not None)
    r = other_thread(lambda: locks.try_acquire(key, td(milliseconds=300), td(seconds=5)))
    P(f"{name} other thread while held -> ", f"{r['r']!r} after {r['dt']:.2f}s")
    inner.release()
    r = other_thread(lambda: locks.try_acquire(key, td(milliseconds=100), td(seconds=5)))
    P(f"{name} other thread after inner close (outer still open)", r["r"])
    lease.release()
    r = other_thread(lambda: locks.try_acquire(key, td(milliseconds=100), td(seconds=5)))
    P(f"{name} other thread after outer close gets it", r["r"] is not None); r["r"] and r["r"].release()
    # first acquire's max_hold governs; a re-entry's longer max_hold is ignored
    k2 = f"n6:{name}:hold"
    outer = locks.acquire(k2, td(seconds=2)); again = locks.acquire(k2, td(seconds=60))
    time.sleep(2.8)
    r = other_thread(lambda: locks.try_acquire(k2, td(milliseconds=500), td(seconds=5)))
    P(f"{name} after outer max_hold=2s (re-entry asked 60s), other thread gets it", r["r"] is not None)
    r["r"] and r["r"].release(); again.release(); outer.release()
    # close() on the provider does nothing: an open lease stays held
    k3 = f"n6:{name}:close"
    held = locks.acquire(k3, td(seconds=30)); locks.close()
    r = other_thread(lambda: locks.try_acquire(k3, td(milliseconds=200), td(seconds=5)))
    P(f"{name} after provider.close(), open lease still excludes", r["r"] is None)
    held.release()
    # an asyncio event loop's thread is refused
    async def on_loop(): return locks.try_acquire(f"n6:{name}:loop", td(milliseconds=10), td(seconds=5))
    try: asyncio.run(on_loop()); P(f"{name} acquire on event loop thread", "allowed")
    except Exception as e: P(f"{name} acquire on event loop thread", f"{type(e).__name__}")

P("watchdog threads after holds", sorted({t.name for t in threading.enumerate() if t.name not in ("MainThread",)} ))

# MySQL vs MariaDB: the sub-second wait (keyed-locks.md table and sentence)
for name in ("mysql", "mariadb"):
    locks = providers[name]; key = f"n6:{name}:round"
    held = other_thread  # placeholder
    h = {}
    ev = threading.Event(); done = threading.Event()
    def holder():
        lz = locks.acquire(key, td(seconds=60)); ev.set(); done.wait(); lz.release()
    th = threading.Thread(target=holder); th.start(); ev.wait()
    for ms in (250, 500, 1000, 1500):
        t0 = time.monotonic(); got = locks.try_acquire(key, td(milliseconds=ms), td(seconds=5)); dt = time.monotonic() - t0
        P(f"{name} try_acquire {ms}ms on a held key waited", f"{dt:.2f}s, got={got is not None}")
    # the lock name Java uses
    with create_engine(URLS[name]).connect() as c:
        jname = format(lock_key_hash("wallet:abc") & 0xFFFFFFFFFFFFFFFF, "x")
        P(f"{name} lock name for wallet:abc (doc says 813e4046e5b48a46)", jname)
        P(f"{name} IS_USED_LOCK of the held key's name is set", c.execute(text("SELECT IS_USED_LOCK(:n)"), {"n": format(lock_key_hash(key) & 0xFFFFFFFFFFFFFFFF, "x")}).scalar() is not None)
    done.set(); th.join()

# unreachable database -> LockAcquisitionError
for name, cls, url in [("pg", PostgresKeyedLockProvider, f"postgresql+psycopg://postgres:{PW}@127.0.0.1:58739/postgres"),
                       ("mysql", MySQLKeyedLockProvider, f"mysql+pymysql://root:{PW}@127.0.0.1:58739/mysql")]:
    try:
        cls(create_engine(url)).try_acquire("x", td(milliseconds=100), td(seconds=5)); P(f"{name} unreachable db", "no error")
    except Exception as e:
        P(f"{name} unreachable db raises", type(e).__name__)

# Redis layout: hash at ekbatan-lock:<key>, TTL = max_hold, a 0 published on release
rc = redis.Redis(host="127.0.0.1", port=58723)
ps = rc.pubsub(); ps.subscribe("redisson_lock__channel:{ekbatan-lock:wallet:abc}"); ps.get_message(timeout=1)
lz = providers["redis"].acquire("wallet:abc", td(seconds=300))
P("redis key type at ekbatan-lock:wallet:abc", rc.type("ekbatan-lock:wallet:abc").decode())
P("redis hash fields (client:thread -> count)", {k.decode().split(":")[-1].isdigit(): v.decode() for k, v in rc.hgetall("ekbatan-lock:wallet:abc").items()})
P("redis PTTL ms (max_hold 300 s)", rc.pttl("ekbatan-lock:wallet:abc"))
lz.release(); time.sleep(0.2)
m = ps.get_message(timeout=2)
P("redis message on release", m and (m["channel"].decode(), m["data"].decode()))

# PG: segments of 5 s while waiting (observe the waiter's query_start changing)
locks = providers["pg"]; key = "n6:pg:segments"; ev = threading.Event(); done = threading.Event()
def holder():
    lz = locks.acquire(key, td(seconds=60)); ev.set(); done.wait(); lz.release()
th = threading.Thread(target=holder); th.start(); ev.wait()
waiter = threading.Thread(target=lambda: locks.acquire(key, td(seconds=30)).release()); waiter.start()
starts = set(); eng = create_engine(URLS["pg"])
for _ in range(24):
    with eng.connect() as c:
        for (qs,) in c.execute(text("SELECT query_start FROM pg_stat_activity WHERE wait_event_type='Lock' AND wait_event='advisory'")):
            starts.add(qs)
    time.sleep(0.5)
P("pg distinct waits seen by the waiter in 12 s (5 s segments -> 3)", len(starts))
# lock_timeout reset after a bounded wait that failed
r = locks.try_acquire(key, td(milliseconds=200), td(seconds=5))
with locks._engine.connect() as c:
    P("pg lock_timeout on a pooled connection after a timed-out try", c.execute(text("SHOW lock_timeout")).scalar())
done.set(); th.join(); waiter.join()
