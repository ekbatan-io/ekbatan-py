"""Follow the complete-project-setup page, as written, in an empty folder - and check it worked.

    uv run python scripts/check-setup-guide.py                  # every database x installer
    uv run python scripts/check-setup-guide.py --db pg --install uv

It reads `website/src/pages/learn/complete-project-setup.mdx`, keeps what the picker would show for
one database and one installer, and in page order writes every file the page shows (each
`CodeBlock` is the example file it imports) and runs every shell command and the Makefile. Then
it checks what the page promises: `make test` passes, the service answers, a deposit raises an
alert, the tables are there, the outbox holds exactly the rows the page's table lists, and the
wheel holds `schema.py`, the config and the migrations - and, installed into a fresh venv away
from the source, imports and finds them. The packaging step runs inside a git repository, made
if the page did not make one, because a real project lives in one and some build tools honour
`.gitignore` only there.

Needs Docker, network, and Python 3.13+ found by `uv python find`. Not part of `make lint`.

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
DATABASES = ("pg", "mysql", "mariadb")
INSTALLERS = ("uv", "pip", "poetry")
CONTAINERS = {"pg": "wallet-pg", "mysql": "wallet-mysql", "mariadb": "wallet-mariadb"}
MIGRATIONS = (
    "V0001__eventlog.sql",
    "V0002__scheduled_tasks.sql",
    "V0003__wallets.sql",
    "V0004__alerts.sql",
)


@dataclass
class Item:
    kind: str  # bash, make, toml, file
    text: str
    path: str | None = None


def read_page(db: str, install: str) -> list[Item]:
    """What the picker shows for (db, install), in page order."""
    lines = PAGE.read_text().splitlines()
    imports = dict(re.findall(r'^import (\w+) from "([^"]+)\?raw";', PAGE.read_text(), re.M))
    stack: list[tuple[str, str] | None] = []
    items: list[Item] = []
    fence: tuple[str, list[str]] | None = None

    def visible() -> bool:
        wanted = {"db": db, "install": install}
        return all(entry is None or wanted[entry[0]] == entry[1] for entry in stack)

    for line in lines:
        if fence is not None:
            if line.startswith("```"):
                kind, body = fence
                if visible() and kind in ("bash", "make", "toml"):
                    items.append(Item(kind, "\n".join(body)))
                fence = None
            else:
                fence[1].append(line)
            continue
        if line.startswith("```"):
            fence = (line[3:].strip(), [])
            continue
        opened = re.match(r'<div data-(db|install)="(\w+)">', line)
        if opened:
            stack.append((opened.group(1), opened.group(2)))
            continue
        if line.startswith("<div"):
            stack.append(None)
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
    for line in command.splitlines():
        specs = re.findall(r'"ekbatan\[([^\]]+)\]"', line)
        if not specs:
            out.append(line)
            continue
        rest = re.sub(r'\s*"ekbatan\[[^\]]+\]"', "", line)
        dev = "--dev" in line or "--group dev" in line
        for extras in specs:
            # The scheduler once, with the runtime dependencies; ekbatan needs it either way.
            if install == "uv":
                flag = "--dev " if dev else ""
                if not dev:
                    out.append(f'uv add -q --editable "{ROOT}/db-scheduler"')
                out.append(f'uv add -q {flag}--editable "ekbatan[{extras}] @ {ROOT}"')
            elif install == "pip":
                if not dev:
                    out.append(f'pip install -q -e "{ROOT}/db-scheduler"')
                out.append(f'pip install -q -e "{ROOT}[{extras}]"')
            else:
                group = "--group dev " if dev else ""
                if not dev:
                    out.append(f'poetry add -q --editable "{ROOT}/db-scheduler"')
                out.append(f'poetry add -q {group}--editable "{ROOT}[{extras}]"')
        command_words = {
            "uv": ["uv", "add", "--dev"],
            "pip": ["pip", "install"],
            "poetry": ["poetry", "add", "--group", "dev"],
        }[install]
        if [word for word in shlex.split(rest) if word not in command_words]:
            out.append(rest)
    return "\n".join(out)


def script_for(db: str, install: str, items: list[Item], python: Path, shims: Path) -> str:
    """One bash script that does what the reader does, step by step."""
    container = CONTAINERS[db]
    cleanup = (
        "pkill -f 'uvicorn --factory wallet.api' || true; "
        f"docker rm -f {container} >/dev/null 2>&1 || true"
    )
    parts = [
        "set -e",
        f'export PATH="{shims}:$PATH"',
        # Whatever happens - a failed step stops the script - nothing is left running.
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
        else:
            text = item.text
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
                extras = {"pg": "postgres", "mysql": "mysql", "mariadb": "mariadb"}[db]
                parts.append(
                    "python3 -m venv ../installed && "
                    f'../installed/bin/pip install -q -e "{ROOT}/db-scheduler" '
                    f'"{ROOT}[{extras},migrations]" fastapi && '
                    "../installed/bin/pip install -q --no-deps dist/*.whl && "
                    '(cd .. && installed/bin/python -c "'
                    "from wallet.app import MIGRATIONS, Settings; import wallet.repository; "
                    "print('== installed', sorted(p.name for p in MIGRATIONS.glob('*.sql')), "
                    'Settings().config_file.is_file())")'
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


def check(output: str, db: str) -> list[str]:
    """What the page promises, against what happened."""
    problems = []
    tested = re.search(r"== make test\n(.*)", output)
    if not tested or "passed" not in tested.group(1) or "failed" in tested.group(1):
        problems.append(f"make test: {tested.group(1) if tested else 'never ran'}")
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


def run(db: str, install: str, keep: bool = False) -> list[str]:
    python = Path(subprocess.check_output(["uv", "python", "find", "3.14"], text=True).strip())
    work = Path(tempfile.mkdtemp(prefix=f"setup-{db}-{install}-"))
    shims = work / "shims"
    shims.mkdir()
    (shims / "python3").symlink_to(python)
    (shims / "poetry").write_text('#!/bin/sh\nexec uvx -q poetry "$@"\n')
    (shims / "poetry").chmod(0o755)
    script = script_for(db, install, read_page(db, install), python, shims)
    (work / "follow.sh").write_text(script)
    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(work / "follow.sh")], cwd=work, capture_output=True, text=True, timeout=1800
    )
    output = result.stdout + result.stderr
    (work / "output.log").write_text(output)
    problems = check(output, db)
    if result.returncode != 0:
        problems.insert(0, f"a command failed (exit {result.returncode}); see {work}/output.log")
    print(
        f"{db:8} {install:7} {'ok' if not problems else 'FAILED'} "
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
    parser.add_argument("--keep", action="store_true", help="keep the folder, even when it worked")
    args = parser.parse_args()
    failed = False
    for db in args.db or DATABASES:
        for install in args.install or INSTALLERS:
            failed |= bool(run(db, install, args.keep))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
