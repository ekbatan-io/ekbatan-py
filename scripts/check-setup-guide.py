"""Follow the complete-project-setup page, as written, in an empty folder - and check it worked.

    uv run python scripts/check-setup-guide.py          # every database x installer x mode: 24
    uv run python scripts/check-setup-guide.py --db pg --install uv --mode async

It reads `website/src/pages/learn/complete-project-setup.mdx`, keeps what the picker would show for
one database, one installer and one mode, and in page order writes every file the page shows (each
`CodeBlock` is the example file it imports) and runs every shell command and the Makefile. Then
it checks what the page promises.

On PostgreSQL, MySQL and MariaDB: `make test` passes, the service answers, a deposit raises an
alert, the tables are there, the outbox holds exactly the rows the page's table lists, and the
wheel holds `schema.py`, the config and the migrations - and, installed into a fresh venv away
from the source, imports and finds them. On SQLite, where the service is a script: `make test`
passes, `make run` delivers the alert and prints every outbox row the page's sample output shows,
and the wheel holds `schema.py` and the config and finds them installed. The packaging step runs
inside a git repository, made if the page did not make one, because a real project lives in one
and some build tools honour `.gitignore` only there.

Needs Docker (not for SQLite), network, and Python 3.13+ found by `uv python find`. Not part of
`make lint`.

**One substitution**, and only one: `ekbatan` is not on PyPI yet, so wherever the page installs
`ekbatan[...]` from the index, this installs it from this checkout instead (with its scheduler,
which is not published either). Everything else runs as the page has it.
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "website" / "src" / "pages" / "learn" / "complete-project-setup.mdx"
DATABASES = ("pg", "mysql", "mariadb", "sqlite")
INSTALLERS = ("uv", "pip", "poetry")
MODES = ("sync", "async")
CONTAINERS = {"pg": "wallet-pg", "mysql": "wallet-mysql", "mariadb": "wallet-mariadb"}
MIGRATIONS = (
    "V0001__eventlog.sql",
    "V0002__scheduled_tasks.sql",
    "V0003__wallets.sql",
    "V0004__alerts.sql",
)


@dataclass
class Item:
    kind: str  # bash, make, toml, file, text (shown output, not run)
    text: str
    path: str | None = None


def read_page(db: str, install: str, mode: str) -> list[Item]:
    """What the picker shows for (db, install, mode), in page order."""
    lines = PAGE.read_text().splitlines()
    imports = dict(re.findall(r'^import (\w+) from "([^"]+)\?raw";', PAGE.read_text(), re.M))
    # One entry per open <div>: its picker attributes, each a space-separated list of values.
    stack: list[dict[str, str]] = []
    items: list[Item] = []
    fence: tuple[str, list[str]] | None = None
    wanted = {"db": db, "install": install, "mode": mode}

    def visible() -> bool:
        return all(
            wanted[dim] in values.split()
            for entry in stack
            for dim, values in entry.items()
            if dim in wanted
        )

    for line in lines:
        if fence is not None:
            if line.startswith("```"):
                kind, body = fence
                if visible() and kind in ("bash", "make", "toml", "text"):
                    items.append(Item(kind, "\n".join(body)))
                fence = None
            else:
                fence[1].append(line)
            continue
        if line.startswith("```"):
            fence = (line[3:].strip(), [])
            continue
        if line.startswith("<div"):
            stack.append(dict(re.findall(r'data-(\w+)="([^"]*)"', line)))
            continue
        if line.startswith("</div>"):
            stack.pop()
            continue
        block = re.match(r'<CodeBlock filename="([^"]+)" .*code=\{(\w+)\}', line)
        if block and visible():
            source = (PAGE.parent / imports[block.group(2)]).resolve()
            items.append(Item("file", source.read_text(), path=block.group(1)))
    return items


def localise(command: str, install: str) -> str:
    """The one substitution: `ekbatan[...]` from this checkout, not from PyPI."""
    out = []
    # `"ekbatan[extras]"`, or a bare `ekbatan` with none - only in an install command.
    spec = re.compile(r'\s*(?:"ekbatan\[([^\]]+)\]"|(?<![\w."\[-])ekbatan(?![\w.\[-]))')
    for line in command.splitlines():
        installing = line.startswith(("uv add", "pip install", "poetry add"))
        specs = [m.group(1) or "" for m in spec.finditer(line)] if installing else []
        if not specs:
            out.append(line)
            continue
        rest = spec.sub("", line)
        dev = "--dev" in line or "--group dev" in line
        for extras in specs:
            wanted = f"[{extras}]" if extras else ""
            # The scheduler once, with the runtime dependencies; ekbatan needs it either way.
            if install == "uv":
                flag = "--dev " if dev else ""
                if not dev:
                    out.append(f'uv add -q --editable "{ROOT}/db-scheduler"')
                out.append(f'uv add -q {flag}--editable "ekbatan{wanted} @ {ROOT}"')
            elif install == "pip":
                if not dev:
                    out.append(f'pip install -q -e "{ROOT}/db-scheduler"')
                out.append(f'pip install -q -e "{ROOT}{wanted}"')
            else:
                group = "--group dev " if dev else ""
                if not dev:
                    out.append(f'poetry add -q --editable "{ROOT}/db-scheduler"')
                out.append(f'poetry add -q {group}--editable "{ROOT}{wanted}"')
        command_words = {
            "uv": ["uv", "add", "--dev"],
            "pip": ["pip", "install"],
            "poetry": ["poetry", "add", "--group", "dev"],
        }[install]
        if [word for word in shlex.split(rest) if word not in command_words]:
            out.append(rest)
    return "\n".join(out)


#: What the installed wheel needs beside itself, per database and mode.
INSTALLED_EXTRAS = {
    ("pg", "sync"): "[postgres,migrations]",
    ("pg", "async"): "[postgres,postgres-async,migrations]",
    ("mysql", "sync"): "[mysql,migrations]",
    ("mysql", "async"): "[mysql,mysql-async,migrations]",
    ("mariadb", "sync"): "[mariadb,migrations]",
    ("mariadb", "async"): "[mariadb,mariadb-async,migrations]",
    ("sqlite", "sync"): "",
    ("sqlite", "async"): "[sqlite-async]",
}


def script_for(
    db: str, install: str, mode: str, items: list[Item], python: Path, shims: Path
) -> str:
    """One bash script that does what the reader does, step by step."""
    # The service, stopped for real before its database goes: a reloader that outlives one
    # run answers the next run's health check from a database that is gone. TERM first, KILL
    # if it will not go, until nothing listens on the port.
    stop_service = (
        "stop_service() { for i in $(seq 1 30); do "
        "pids=$( (lsof -ti tcp:8000 -sTCP:LISTEN; pgrep -f 'uvicorn --factory wallet.api') "
        '2>/dev/null | sort -u); [ -z "$pids" ] && return 0; '
        "if [ $i -lt 15 ]; then kill $pids 2>/dev/null || true; "
        "else kill -9 $pids 2>/dev/null || true; fi; "
        "sleep 1; done; }"
    )
    cleanup = "stop_service"
    if db in CONTAINERS:
        cleanup += f"; docker rm -f {CONTAINERS[db]} >/dev/null 2>&1 || true"
    parts = [
        "set -e",
        # A reader's fresh shell has no virtualenv active. This one is started by `uv run`, and
        # Poetry installs into whatever VIRTUAL_ENV names - this repository's own environment.
        "unset VIRTUAL_ENV",
        "export PATH=\"$(echo \"$PATH\" | tr ':' '\\n' "
        f"| grep -v '^{ROOT}/.venv' | paste -sd: -)\"",
        f'export PATH="{shims}:$PATH"',
        # Whatever happens - a failed step stops the script - nothing is left running.
        stop_service,
        cleanup,
        f'trap "{cleanup}" EXIT',
    ]
    for item in items:
        if item.kind == "file":
            parts.append(
                f"mkdir -p $(dirname {item.path})\n"
                f"cat > {item.path} <<'EKBATAN_EOF'\n{item.text}\nEKBATAN_EOF"
            )
        elif item.kind == "make":
            parts.append(f"cat > Makefile <<'EKBATAN_EOF'\n{item.text}\nEKBATAN_EOF")
        elif item.kind == "toml":
            parts.append(f"cat > pyproject.toml <<'EKBATAN_EOF'\n{item.text}\nEKBATAN_EOF")
        elif item.kind == "text":
            continue
        else:
            text = item.text
            if text.strip() == "make run" and db == "sqlite":
                # A script, not a server: it runs, narrates and exits.
                parts.append(
                    "make run > run.log 2>&1 || { cat run.log; exit 1; }\n"
                    'echo "== run"; cat run.log'
                )
                continue
            if text.strip() == "make run":
                parts.append(
                    "(make run > run.log 2>&1 &)\n"
                    "for i in $(seq 1 120); do\n"
                    "  curl -s localhost:8000/health >/dev/null 2>&1 && break; sleep 1\n"
                    "done\n"
                    'echo "== health $(curl -s localhost:8000/health)"'
                )
                continue
            if text.startswith(("uv build", "pip wheel", "poetry build")):
                # A real project lives in a repository, and some build tools honour .gitignore
                # only inside one - so the packaging is checked in one.
                parts.append(
                    "[ -d .git ] || { git init -q && git add -A >/dev/null 2>&1 && "
                    "git -c user.email=check@example.com -c user.name=check commit -qm init; }"
                )
                build, listing = text.splitlines()
                parts.append(build)
                parts.append(f'echo "== wheel"; {listing}')
                # And the wheel works on its own: installed where no source tree is next to it.
                extras = INSTALLED_EXTRAS[(db, mode)]
                web = "" if db == "sqlite" else " fastapi"
                finds = (
                    "from wallet.app import CONFIG_FILE; import wallet.repository; "
                    "print('== installed', CONFIG_FILE.is_file())"
                    if db == "sqlite"
                    else "from wallet.app import MIGRATIONS, Settings; import wallet.repository; "
                    "print('== installed', sorted(p.name for p in MIGRATIONS.glob('*.sql')), "
                    "Settings().config_file.is_file())"
                )
                parts.append(
                    "python3 -m venv ../installed && "
                    f'../installed/bin/pip install -q -e "{ROOT}/db-scheduler" '
                    f'"{ROOT}{extras}"{web} && '
                    "../installed/bin/pip install -q --no-deps dist/*.whl && "
                    f'(cd .. && installed/bin/python -c "{finds}")'
                )
                continue
            if text.strip() == "make test":
                parts.append('echo "== make test"; make test 2>&1 | tail -1')
                continue
            if text.startswith("docker exec"):
                # The service answered only after its startup migrations ran, so the tables
                # and the outbox are there to read.
                parts.append(
                    f'echo "== {text.splitlines()[0][:40]}"; {text} 2>&1 | grep -v Warning'
                )
                continue
            if text.startswith("WALLET=$("):
                parts.append(text.rsplit("\n\ncurl -s localhost:8000/wallets/$WALLET/alerts", 1)[0])
                parts.append(
                    "for i in $(seq 1 30); do n=$(curl -s localhost:8000/wallets/$WALLET/alerts "
                    "| python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'); "
                    '[ "${n:-0}" -ge 1 ] && break; sleep 1; done\n'
                    'echo "== alerts $(curl -s localhost:8000/wallets/$WALLET/alerts)"'
                )
                continue
            parts.append(localise(text, install))
    return "\n\n".join(parts)


def check(output: str, db: str, items: list[Item]) -> list[str]:
    """What the page promises, against what happened."""
    problems = []
    tested = re.search(r"== make test\n(.*)", output)
    if not tested or "passed" not in tested.group(1) or "failed" in tested.group(1):
        problems.append(f"make test: {tested.group(1) if tested else 'never ran'}")
    if db == "sqlite":
        return problems + check_script(output, items)
    if '== health {"status":"ok"}' not in output:
        problems.append("the service did not answer")
    if not re.search(r'== alerts \[\{.*"recipient":"ada@example.com"', output):
        problems.append("no alert after the deposit")
    for table in ("alerts", "event_notifications", "events", "scheduled_tasks", "wallets"):
        if not re.search(rf"\b{table}\b", output):
            problems.append(f"table {table} missing")
    wheel = output.split("== wheel", 1)[1] if "== wheel" in output else ""
    for packed in ("schema.py", "ekbatan.yaml", *MIGRATIONS):
        if packed not in wheel:
            problems.append(f"{packed} is not in the wheel")
    installed = f"== installed {list(MIGRATIONS)} True"
    if installed not in output:
        problems.append("the installed wheel does not find its migrations and config")
    expected = page_outbox(db)
    rows = [
        tuple(cell.strip() for cell in re.split(r"\t|\|", line) if cell.strip() != "")
        for line in output.splitlines()
        if line.strip().startswith(("OpenWalletAction", "DepositMoneyAction", "RaiseAlertAction"))
    ]
    if [tuple(c for c in row if c) for row in rows] != [
        tuple(c for c in row if c) for row in expected
    ]:
        problems.append(f"outbox: page says {expected}, database has {rows}")
    return problems


#: An outbox line of the SQLite script's narration: the action, then the event or the sentinel.
OUTBOX_LINE = re.compile(r"^\s+(\w+Action)\s+(\w+Event|\(none - a sentinel\))", re.M)


def check_script(output: str, items: list[Item]) -> list[str]:
    """SQLite: the script delivered the alert and printed every outbox row the page shows."""
    problems = []
    ran = output.split("== run", 1)[1] if "== run" in output else ""
    if "alert for ada@example.com" not in ran:
        problems.append("the script printed no alert after the deposit")
    shown = [item.text for item in items if item.kind == "text"]
    if len(shown) != 1:
        problems.append(f"the page shows {len(shown)} sample outputs for this choice, not one")
    else:
        expected = set(OUTBOX_LINE.findall(shown[0]))
        missing = expected - set(OUTBOX_LINE.findall(ran))
        if not expected or missing:
            problems.append(f"outbox: page shows {sorted(expected)}, missing {sorted(missing)}")
    wheel = output.split("== wheel", 1)[1] if "== wheel" in output else ""
    for packed in ("schema.py", "ekbatan.yaml"):
        if packed not in wheel:
            problems.append(f"{packed} is not in the wheel")
    if "== installed True" not in output:
        problems.append("the installed wheel does not find its config")
    return problems


def page_outbox(db: str) -> list[tuple[str, ...]]:
    """The outbox table the page shows for this database."""
    text = PAGE.read_text()
    block = text.split(f'<div data-db="{db}">')
    for chunk in block:
        head = chunk.split("</div>")[0]
        if "| action_name |" in head:
            rows = [r for r in head.splitlines() if r.startswith("| `")]
            return [tuple(c.strip().strip("`") for c in r.strip("|").split("|")) for r in rows]
    return []


def run(db: str, install: str, mode: str, keep: bool = False) -> list[str]:
    python = Path(subprocess.check_output(["uv", "python", "find", "3.14"], text=True).strip())
    work = Path(tempfile.mkdtemp(prefix=f"setup-{db}-{install}-{mode}-"))
    shims = work / "shims"
    shims.mkdir()
    (shims / "python3").symlink_to(python)
    (shims / "poetry").write_text('#!/bin/sh\nexec uvx -q poetry "$@"\n')
    (shims / "poetry").chmod(0o755)
    items = read_page(db, install, mode)
    script = script_for(db, install, mode, items, python, shims)
    (work / "follow.sh").write_text(script)
    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(work / "follow.sh")], cwd=work, capture_output=True, text=True, timeout=1800
    )
    output = result.stdout + result.stderr
    (work / "output.log").write_text(output)
    problems = check(output, db, items)
    if result.returncode != 0:
        problems.insert(0, f"a command failed (exit {result.returncode}); see {work}/output.log")
    print(
        f"{db:8} {install:7} {mode:6} {'ok' if not problems else 'FAILED'} "
        f"in {time.monotonic() - started:.0f}s  ({work})"
    )
    for problem in problems:
        print("    " + textwrap.shorten(problem, 300))
    if not problems and not keep:
        shutil.rmtree(work, ignore_errors=True)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", choices=DATABASES, action="append")
    parser.add_argument("--install", choices=INSTALLERS, action="append")
    parser.add_argument("--mode", choices=MODES, action="append")
    parser.add_argument("--keep", action="store_true", help="keep the folder, even when it worked")
    args = parser.parse_args()
    failed = False
    for db in args.db or DATABASES:
        for install in args.install or INSTALLERS:
            for mode in args.mode or MODES:
                failed |= bool(run(db, install, mode, args.keep))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
