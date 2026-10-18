"""Parity scan area E: how long a bounded wait on a held key really takes (Python side of
ParityEWaitProbeTest.java)."""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from locks_probe import bucket, other_try_timed, p, provider, uk  # noqa: E402

for b in ("mysql", "mariadb", "redis"):
    l = provider(b, 4)
    k = uk(b)
    lease = l.acquire(k, timedelta(minutes=5))
    for w in (250, 5000):
        got, ms = other_try_timed(l, k, timedelta(milliseconds=w))
        p(f"{b}.wait_{w}ms.got", got)
        p(f"{b}.wait_{w}ms.elapsed_bucket_ms", bucket(ms))
    lease.release()
