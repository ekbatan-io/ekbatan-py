"""Times each step of stopping the app built with a wrong password (probe_d's d2), to see where
Python's 20 s go."""
import sys, time, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_d
from probe_d import Whole, WRONG, quiet, p
quiet()
bad = Whole(WRONG, [])
try:
    bad.jobs.start()
except Exception:
    pass
for name, step in (("jobs.stop", bad.jobs.stop), ("jobs_engine.dispose", bad.jobs_engine.dispose),
                   ("locks.close", bad.locks.close), ("lock_engine.dispose", bad.lock_engine.dispose),
                   ("reg.close", bad.reg.close)):
    t = time.monotonic(); step(); p("d3.step." + name, probe_d.secs(time.monotonic() - t))
p("d3.threads_after", sorted(t.name for t in threading.enumerate()))
