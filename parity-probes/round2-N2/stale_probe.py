"""Area N2: a schema.py generated, then a migration added without regenerating. Python only;
the Java twin is `./gradlew generateJooqClasses` rerunning by itself (see stale.java.out)."""
import importlib.util
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent / "stale"
MIG = HERE / "migrations"
OUT = HERE / "schema.py"
extra = MIG / "V0002__more.sql"
extra.unlink(missing_ok=True)
subprocess.run([sys.executable, "-m", "ekbatan.schemagen", "--migrations", str(MIG), "--out", str(OUT)], check=True, capture_output=True)


def load():
    spec = importlib.util.spec_from_file_location("s", OUT)
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
        return "OK " + ",".join(c.name for c in m.THINGS.columns)
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {str(e).splitlines()[0]}"


print("PROBE stale.fresh.import:", load())
extra.write_text("ALTER TABLE things ADD COLUMN added_later TEXT;\n")
print("PROBE stale.afterNewMigration.import:", load())
extra.unlink()
print("PROBE stale.migrationRemovedAgain.import:", load())
