"""docs/database/config-files.md: every config block loaded as written, plus the placeholder rules."""
import os, tempfile, textwrap
from pathlib import Path
from common import probe
from ekbatan.config_files import load_sharding_config, load_ekbatan_config
here = Path(__file__).parent / "cfg"
env = {"DB_PASSWORD": "N6-Secret-Marker"}
for f in sorted(here.iterdir()):
    if f.name == "block4.yaml":   # the "# ... as above" whole-section block
        probe(f"config-files.md {f.name} load_ekbatan_config (as written, with '# ... as above')", lambda: load_ekbatan_config(f, environ=env))
        continue
    probe(f"config-files.md {f.name} load_sharding_config", lambda: (lambda l: (l.default_shard, [(m.config_for('primaryConfig').pool_size, m.config_for('primaryConfig').password == env['DB_PASSWORD'], dict(m.config_for('primaryConfig').data_source_properties)) for g in l.groups for m in g.members]))(load_sharding_config(f, environ=env)))
d = Path(tempfile.mkdtemp())
base = (here / "block0.yaml").read_text()
def y(text, environ=env):
    p = d / "x.yaml"; p.write_text(text); return load_sharding_config(p, environ=environ)
probe("unset variable with no default stops the load", lambda: y(base, environ={}))
probe("${DB_PASSWORD:} gives empty", lambda: y(base.replace("${DB_PASSWORD}", "${DB_PASSWORD:}"), environ={}).groups[0].members[0].config_for("primaryConfig").password)
probe("value with % and $ inserted as is", lambda: y(base, environ={"DB_PASSWORD": "a%b$c${X}"}).groups[0].members[0].config_for("primaryConfig").password == "a%b$c${X}")
probe("INI-style placeholder in YAML refused", lambda: y(base.replace("${DB_PASSWORD}", "'%(ENV_DB_PASSWORD)s'")))
probe("pool-size: \"20\" refused", lambda: y(base.replace("pool-size: 20", 'pool-size: "20"')))
probe("unknown key refused with path", lambda: y(base.replace("pool-size: 20", "pool-sise: 20")))
probe("bare-number duration refused", lambda: load_ekbatan_config((lambda p: (p.write_text("ekbatan:\n  sharding:\n" + textwrap.indent(base.split("sharding:\n",1)[1], "") + "  jobs:\n    pollingInterval: 10\n"), p)[1])(d / "j.yaml"), environ=env))
probe("namespace default", lambda: load_ekbatan_config((lambda p: (p.write_text(base), p)[1])(d / "n.yaml"), environ=env).namespace)
