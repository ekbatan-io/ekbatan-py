"""Run the distributed-jobs claims in docs/jobs/distributed-jobs.md on SQLite."""
import atexit, logging, threading, time, io
from datetime import timedelta
from sqlalchemy import MetaData, create_engine, inspect as sa_inspect
from db_scheduler.task.schedule import FixedDelay
from db_scheduler import builder as dbs_builder
from ekbatan import DataSource
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import DistributedJob, job_registry, EKBATAN_HEARTBEAT_INTERVAL
from ekbatan.schema import scheduled_tasks_table

buf = io.StringIO()
h = logging.StreamHandler(buf); h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.INFO)

engine = create_engine("sqlite:///" + __file__.replace(".py", ".db"))
source = DataSource.from_config(DataSourceConfig(url="sqlite:///" + __file__.replace(".py", ".db")))
md = MetaData(); t = scheduled_tasks_table(md); md.drop_all(engine); md.create_all(engine)
print("PROBE scheduled_tasks.execution_time type:", sa_inspect(engine).get_columns("scheduled_tasks")[[c["name"] for c in sa_inspect(engine).get_columns("scheduled_tasks")].index("execution_time")]["type"])
print("PROBE schema execution_time sqlalchemy type:", repr(t.c.execution_time.type))

started = threading.Event(); seen = {}
# The doc's own shape first: `name = "pay-stipend"` as a class attribute.
class DocShapeJob(DistributedJob):
    name = "pay-stipend"
    def schedule(self): return FixedDelay(timedelta(hours=24))
    def execute(self, context): pass
try:
    job_registry().data_source(source).with_jobs([DocShapeJob()]).build()
    print("PROBE doc snippet `name = \"pay-stipend\"`: builds")
except Exception as e:
    print("PROBE doc snippet `name = \"pay-stipend\"`:", type(e).__name__, e)

class SlowJob(DistributedJob):
    def name(self): return "slow"
    def schedule(self): return FixedDelay(timedelta(hours=24))
    def execute(self, context):
        seen["fraction_dead"] = context.currently_executing.heartbeat_state.fraction_dead
        seen["is_shutting_down_attr"] = hasattr(context.scheduler_state, "is_shutting_down")
        started.set(); time.sleep(4)
        seen["finished"] = True

class Other(DistributedJob):
    def name(self): return "other"
    def schedule(self): return FixedDelay(timedelta(hours=24))
    def execute(self, context): pass

# defaults, per the knob table
b = job_registry().data_source(source).with_jobs([SlowJob(), Other()])
print("PROBE default poll_interval:", b._poll_interval)
print("PROBE default heartbeat_interval:", b._heartbeat_interval, "(db-scheduler's own:", dbs_builder.DEFAULT_HEARTBEAT_INTERVAL, ")")
print("PROBE default shutdown_max_wait (db-scheduler):", dbs_builder.SHUTDOWN_MAX_WAIT)
print("PROBE default missed_heartbeats_limit:", dbs_builder.DEFAULT_MISSED_HEARTBEATS_LIMIT)
seen_builder = {}
b.customize_scheduler(lambda sb: seen_builder.update(threads=sb.executor_threads))
try:
    job_registry().data_source(source).with_jobs([Other()]).customize_scheduler(lambda sb: sb.missed_heartbeats_limit(3)).build()
    print("PROBE missed_heartbeats_limit(3): accepted")
except Exception as e:
    print("PROBE missed_heartbeats_limit(3):", type(e).__name__, e)

reg = b.poll_interval(timedelta(milliseconds=200)).shutdown_max_wait(timedelta(seconds=1)).build()
print("PROBE default workers (2 jobs):", seen_builder["threads"])
reg.start()
print("PROBE stop_on_exit default registered:", reg._stop_on_exit)
started.wait(10)
print("PROBE fraction_dead right after start:", seen.get("fraction_dead"))
print("PROBE ctx.scheduler_state.is_shutting_down exists:", seen.get("is_shutting_down_attr"))
t0 = time.monotonic(); reg.stop(); dt = time.monotonic() - t0
print(f"PROBE stop() with 1s shutdown_max_wait returned after: {dt:.1f}s; job finished by then: {seen.get('finished', False)}")
for line in buf.getvalue().splitlines():
    if "interrupt" in line.lower() or "running" in line.lower():
        print("PROBE stop log:", line[:200])

# register_shutdown_hook off the main thread
out = {}
def off_main():
    try:
        r = job_registry().data_source(source).with_jobs([Other()]).register_shutdown_hook().build(); r.start(); r.stop(); out["r"] = "no error"
    except Exception as e:
        out["r"] = f"{type(e).__name__}: {e}"
th = threading.Thread(target=off_main); th.start(); th.join()
print("PROBE register_shutdown_hook off main thread:", out["r"][:160])
