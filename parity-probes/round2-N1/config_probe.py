"""N1: the same config file content fed to Python's load_ekbatan_config as the Java probe feeds
Spring Boot (N1ConfigProbeTest.java). Prints `PROBE <scenario>.<key>: <value>`."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from ekbatan.config_files import load_ekbatan_config

HERE = Path(__file__).parent / "scenarios"
MARKERS = {"fallback-marker", "ns", "${N1_LITERAL}", "\\${N1_LITERAL}", "$${N1_LITERAL}", "u", "from-env"}


def p(key: str, value: object) -> None:
    print(f"PROBE {key}: {value}")


def iso(d: timedelta | None) -> str:
    if d is None:
        return "-"
    total = d.total_seconds()
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    out = "PT"
    if hours:
        out += f"{int(hours)}H"
    if minutes:
        out += f"{int(minutes)}M"
    if seconds or out == "PT":
        out += f"{seconds:g}S"
    return out


def run(label: str, file: str, extra_env: dict[str, str]) -> None:
    text = (HERE / f"{file}.yaml").read_text()
    text = (
        text.replace("@URL_KEY@", "url")
        .replace("@URL@", "postgresql+psycopg://localhost:5432/db")
        .replace("@POOL_KEY@", "poolSize")
    )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
        handle.write(text)
    env = dict(os.environ) | extra_env
    try:
        config = load_ekbatan_config(handle.name, environ=env)
    except Exception as error:  # noqa: BLE001
        message = str(error).replace(handle.name, "<file>").replace(Path(handle.name).name, "<file>")
        p(f"{label}.load", f"error {type(error).__name__}: {message.splitlines()[0][:220]}")
        return
    finally:
        os.unlink(handle.name)
    p(f"{label}.namespace", f"[{config.namespace}]")
    c = config.sharding.groups[0].members[0].config_for("primaryConfig")
    p(f"{label}.pool", c.pool_size)
    p(f"{label}.username", c.username or "<none>")
    pw = c.password
    p(f"{label}.password", "<none>" if pw is None else (pw if pw in MARKERS else f"<other len {len(pw)}>"))
    p(f"{label}.dsp", dict(sorted(c.data_source_properties.items())))
    j = config.jobs
    p(
        f"{label}.jobs",
        f"polling={iso(j.polling_interval)} heartbeat={iso(j.heartbeat_interval)} "
        f"shutdown={iso(j.shutdown_max_wait)}",
    )
    leh = config.local_event_handler
    p(
        f"{label}.leh",
        f"fanoutBatch={leh.fanout_batch_size if leh.fanout_batch_size is not None else '-'} "
        f"handlingPoll={iso(leh.handling_poll_delay)} enabled={str(leh.handling.enabled).lower()}",
    )


def main() -> None:
    for f in sorted(HERE.glob("*.yaml")):
        run(f.stem, f.stem, {})
    run(
        "s99_env_override",
        "s00_base",
        {
            "EKBATAN_NAMESPACE": "from-env",
            "EKBATAN_JOBS_POLLINGINTERVAL": "PT7S",
            "EKBATAN_JOBS_POLLING_INTERVAL": "PT7S",
            "EKBATAN_SHARDING_GROUPS_0_MEMBERS_0_CONFIGS_PRIMARYCONFIG_MAXIMUMPOOLSIZE": "7",
            "EKBATAN_LOCALEVENTHANDLER_HANDLING_ENABLED": "true",
        },
    )


if __name__ == "__main__":
    sys.exit(main())
