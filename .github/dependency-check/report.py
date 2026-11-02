"""Writes the dependency report of .github/workflows/dependency-check.yml.

    python3 report.py --out report.md --state state.json --snapshot snapshot.json

The packages are the ones pinned in uv.lock and db-scheduler/uv.lock, transitive ones included.

- Known vulnerabilities: every pinned package, looked up on osv.dev. "Shipped to users" is what a
  published package - ekbatan or ekbatan-py-db-scheduler - depends on, with any extra; the rest,
  only in a dependency group (tests, linters, the examples' web stack), is tests and tools.
- Newer versions: each declared dependency against its releases on PyPI. A release counts only
  once it is MIN_AGE old, the wait `exclude-newer` gives uv in pyproject.toml; until then it is
  listed as waiting, with the day it counts from, and doesn't keep the issue open. Those
  held-back.txt names are listed apart, with the reason.
- --snapshot also writes the same packages as a GitHub dependency submission, for the workflow
  to send: GitHub reads no uv.lock by itself, so without it Dependabot alerts see none of them.

The report ends with an HTML comment listing the advisories found; issue.py compares it with the
previous report's to tell which are new. state.json holds the same list and whether the report is
clean, for issue.py.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import pathlib
import re
import subprocess
import tomllib
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCKS = ("uv.lock", "db-scheduler/uv.lock")
# A newer release counts only once it is this old: a broken or hijacked release is usually found
# and pulled before then. Keep it the same as `exclude-newer` in pyproject.toml and `cooldown` in
# .github/dependabot.yml.
MIN_AGE = datetime.timedelta(days=14)
OSV = "https://api.osv.dev/v1"
PYPI = "https://pypi.org/pypi"
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MEDIUM": 2, "LOW": 3}
# A final release: `2.1.5`, `1.0.post1`. Anything with a pre-release or dev part is left out.
STABLE = re.compile(r"^(\d+(?:\.\d+)*)(?:\.post(\d+))?$")


def norm(name: str) -> str:
    """A package name as PyPI compares them: `SQLAlchemy` and `sqlalchemy` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def version_key(version: str) -> tuple:
    """Enough of a version to tell which of two releases is newer."""
    match = STABLE.match(version)
    if not match:
        numbers = [int(part) for part in re.findall(r"\d+", version)]
        return (tuple(numbers + [0] * (6 - len(numbers))), -1)
    numbers = [int(part) for part in match.group(1).split(".")]
    return (tuple(numbers + [0] * (6 - len(numbers))), int(match.group(2) or 0))


def is_major(current: str, latest: str) -> bool:
    return version_key(current)[0][0] != version_key(latest)[0][0]


def cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def current_cell(entry: dict) -> str:
    """The versions a dependency is on now, oldest first."""
    return ", ".join(sorted(entry["current"], key=version_key))


def list_cell(items: set[str], shown: int = 4) -> str:
    names = sorted(items)
    text = ", ".join(names[:shown])
    return text + (f" and {len(names) - shown} more" if len(names) > shown else "")


# --- the lock files --------------------------------------------------------------------------


class Lock:
    """One uv.lock: its packages, its own project, and which packages each declared dependency
    brings in."""

    def __init__(self, path: pathlib.Path):
        data = tomllib.loads(path.read_text())
        self.file = path.relative_to(ROOT).as_posix()
        self.by_name: dict[str, list[dict]] = {}
        for package in data.get("package", []):
            self.by_name.setdefault(norm(package["name"]), []).append(package)
        self.project = next(
            p for p in data["package"] if (p.get("source") or {}).get("editable") == "."
        )
        self.name = self.project["name"]
        metadata = self.project.get("metadata") or {}
        # (declared name, its range, where: "" for the package itself, "[extra]", or "group").
        self.declared: list[tuple[str, str, str]] = []
        for requirement in metadata.get("requires-dist", []):
            extra = re.search(r"extra == '([^']+)'", requirement.get("marker", ""))
            self.declared.append(
                (
                    norm(requirement["name"]),
                    requirement.get("specifier", ""),
                    f"[{extra.group(1)}]" if extra else "",
                )
            )
        for group, requirements in (metadata.get("requires-dev") or {}).items():
            for requirement in requirements:
                self.declared.append(
                    (norm(requirement["name"]), requirement.get("specifier", ""), f"group {group}")
                )
        shipped = list(self.project.get("dependencies", []))
        for requirements in (self.project.get("optional-dependencies") or {}).values():
            shipped += requirements
        self.shipped = self.reach(shipped)

    def registry(self) -> list[tuple[str, str]]:
        return [
            (name, package["version"])
            for name, packages in self.by_name.items()
            for package in packages
            if "registry" in (package.get("source") or {})
        ]

    def local(self, name: str) -> bool:
        return all("registry" not in (p.get("source") or {}) for p in self.by_name.get(name, []))

    def reach(self, starts: list[dict]) -> set[str]:
        """Every package name the given dependencies bring in, following each one's extras."""
        found, seen = set(), set()
        stack = [(norm(d["name"]), tuple(d.get("extra", []))) for d in starts]
        while stack:
            name, extras = stack.pop()
            if (name, extras) in seen:
                continue
            seen.add((name, extras))
            found.add(name)
            for package in self.by_name.get(name, []):
                edges = list(package.get("dependencies", []))
                for extra in extras:
                    edges += (package.get("optional-dependencies") or {}).get(extra, [])
                stack += [(norm(e["name"]), tuple(e.get("extra", []))) for e in edges]
        return found

    def brought_in_by(self, name: str) -> list[str]:
        """The declared dependencies that bring `name` in; empty if it is declared itself."""
        declared = {d for d, _, _ in self.declared}
        if name in declared:
            return []
        return sorted(d for d in declared if name in self.reach([{"name": d}]))

    def snapshot(self) -> dict:
        """This lock as one manifest of a GitHub dependency submission."""
        declared = {d for d, _, _ in self.declared}
        resolved = {}
        for name, version in self.registry():
            children = set()
            for package in self.by_name[name]:
                if package["version"] != version:
                    continue
                for edge in package.get("dependencies", []):
                    child = norm(edge["name"])
                    children.update(
                        f"{child}@{p['version']}"
                        for p in self.by_name.get(child, [])
                        if "registry" in (p.get("source") or {})
                    )
            resolved[f"{name}@{version}"] = {
                "package_url": f"pkg:pypi/{name}@{version}",
                "relationship": "direct" if name in declared else "indirect",
                "scope": "runtime" if name in self.shipped else "development",
                "dependencies": sorted(children),
            }
        return {"name": self.file, "file": {"source_location": self.file}, "resolved": resolved}


def held_back(path: pathlib.Path) -> list[tuple[str, str, str]]:
    """(name pattern, version pattern, reason) for each line of held-back.txt."""
    entries = []
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                name, version, reason = line.split(None, 2)
                entries.append((name, version, reason))
    return entries


def held_back_reason(entries, name: str, version: str) -> str | None:
    for name_pattern, version_pattern, reason in entries:
        if fnmatch.fnmatchcase(name, norm(name_pattern)) and fnmatch.fnmatchcase(
            version, version_pattern
        ):
            return reason
    return None


# --- newer versions --------------------------------------------------------------------------


def pypi_releases(name: str) -> dict[str, datetime.date]:
    """Each final, not yanked release of `name` on PyPI, with the day it came out."""
    try:
        with urllib.request.urlopen(f"{PYPI}/{name}/json", timeout=60) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {}
    out = {}
    for version, files in (data.get("releases") or {}).items():
        files = [f for f in files if not f.get("yanked")]
        if files and STABLE.match(version):
            first = min(f["upload_time_iso_8601"] for f in files)
            out[version] = datetime.datetime.fromisoformat(first.replace("Z", "+00:00")).date()
    return out


def satisfies(version: str, specifier: str) -> bool | None:
    """Whether `version` is inside a range like `>=9.0,<10`; None if the range can't be read."""
    for part in filter(None, (p.strip() for p in specifier.split(","))):
        match = re.match(r"^(~=|==|!=|>=|<=|>|<)\s*([\w.*+!-]+)$", part)
        if not match or "*" in match.group(2):
            return None
        op, bound = match.groups()
        a, b = version_key(version), version_key(bound)
        if op == "~=":
            prefix = bound.split(".")[:-1]
            ok = a >= b and version.split(".")[: len(prefix)] == prefix
        else:
            ok = {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[
                op
            ]
        if not ok:
            return False
    return True


def newer_versions(locks: list[Lock], entries, today: datetime.date):
    """For each declared dependency with a newer release: the release to move to (or none yet),
    the younger ones, and the ones held back on purpose."""
    declared: dict[str, dict] = {}
    for lock in locks:
        for name, specifier, where in lock.declared:
            if lock.local(name):
                continue
            entry = declared.setdefault(name, {"current": set(), "ranges": set(), "where": set()})
            entry["current"].update(v for n, v in lock.registry() if n == name)
            if specifier:
                entry["ranges"].add(specifier)
            entry["where"].add(f"`{lock.name}`" + (f" {where}" if where else ""))
    updates, waiting, kept = {}, {}, {}
    for name, entry in sorted(declared.items()):
        if not entry["current"]:
            continue
        current = max(entry["current"], key=version_key)
        releases = pypi_releases(name)
        newer = sorted(
            (v for v in releases if version_key(v) > version_key(current)),
            key=version_key,
            reverse=True,
        )
        if not newer:
            continue
        reason = held_back_reason(entries, name, newer[0])
        if reason:
            kept[name] = dict(entry, latest=newer[0], reason=reason)
            continue
        young = []
        for version in newer:
            if held_back_reason(entries, name, version):
                continue
            if today - releases[version] >= MIN_AGE:
                updates[name] = dict(entry, latest=version, young=young)
                break
            young.append((version, releases[version] + MIN_AGE))
        else:
            if young:
                waiting[name] = dict(entry, young=young)
    return updates, waiting, kept


# --- known vulnerabilities -------------------------------------------------------------------


def osv_post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{OSV}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def osv_get(path: str) -> dict:
    with urllib.request.urlopen(f"{OSV}/{path}", timeout=60) as response:
        return json.load(response)


def vulnerabilities(packages: list[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
    keys = sorted(set(packages))
    hits: dict[tuple[str, str], list[str]] = {}
    for start in range(0, len(keys), 500):
        chunk = keys[start : start + 500]
        queries = [{"package": {"ecosystem": "PyPI", "name": n}, "version": v} for n, v in chunk]
        results = osv_post("querybatch", {"queries": queries})["results"]
        for key, result in zip(chunk, results, strict=True):
            ids = [vulnerability["id"] for vulnerability in result.get("vulns") or []]
            if ids:
                hits[key] = ids
    details = {
        vid: osv_get(f"vulns/{vid}")
        for vid in sorted({vid for ids in hits.values() for vid in ids})
    }
    return {
        key: [details[vid] for vid in ids if not details[vid].get("withdrawn")]
        for key, ids in hits.items()
        if any(not details[vid].get("withdrawn") for vid in ids)
    }


def fixed_in(advisory: dict, name: str, version: str) -> str:
    fixes = []
    for affected in advisory.get("affected", []):
        if norm((affected.get("package") or {}).get("name", "")) != name:
            continue
        for affected_range in affected.get("ranges", []):
            fixes += [
                event["fixed"] for event in affected_range.get("events", []) if "fixed" in event
            ]
    later = sorted(
        (fix for fix in fixes if version_key(fix) > version_key(version)), key=version_key
    )
    return later[0] if later else "no fix yet"


def severity(advisory: dict) -> str:
    return ((advisory.get("database_specific") or {}).get("severity") or "-").upper()


def advisory_rows(found: dict, keys: list[tuple[str, str]], locks: list[Lock]) -> list[str]:
    rows = []
    for name, version in keys:
        through = sorted(
            {
                d
                for lock in locks
                if any(n == name and v == version for n, v in lock.registry())
                for d in lock.brought_in_by(name)
            }
        )
        first = f"`{name}`" + (
            " (through " + list_cell({f"`{d}`" for d in through}, 3) + ")" if through else ""
        )
        for advisory in sorted(
            found[(name, version)], key=lambda a: (SEVERITY_ORDER.get(severity(a), 4), a["id"])
        ):
            cve = next(
                (alias for alias in advisory.get("aliases", []) if alias.startswith("CVE-")), ""
            )
            title = advisory.get("summary") or advisory.get("details", "").split(". ")[0]
            link = f"[{advisory['id']}](https://osv.dev/vulnerability/{advisory['id']})"
            rows.append(
                f"| {first} | {version if first else ''} | {link}{f' ({cve})' if cve else ''} "
                f"| {severity(advisory)} | {fixed_in(advisory, name, version)} "
                f"| {cell(title[:160])} |"
            )
            first = ""
    return rows


# --- the report ------------------------------------------------------------------------------


def write_report(locks: list[Lock], held_back_file: pathlib.Path, today: datetime.date):
    entries = held_back(held_back_file)
    updates, waiting, kept = newer_versions(locks, entries, today)
    packages = sorted({p for lock in locks for p in lock.registry()})
    found = vulnerabilities(packages)
    shipped = {(n, v) for lock in locks for n, v in lock.registry() if n in lock.shipped}

    run = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run = (
            f" ([run]({os.environ.get('GITHUB_SERVER_URL')}/{os.environ.get('GITHUB_REPOSITORY')}"
            f"/actions/runs/{os.environ['GITHUB_RUN_ID']}))"
        )
    commit = os.environ.get("GITHUB_SHA", "")[:7]
    lines = [
        "# Dependency report",
        "",
        f"Checked on {datetime.date.today().isoformat()}{' at ' + commit if commit else ''}{run}, "
        "by `.github/workflows/dependency-check.yml`: every Monday, and on each push to `main` "
        "that changes the dependencies. The examples under `ekbatan-py-examples/` are separate "
        "projects with no "
        "lock file of their own, and are not checked.",
        "",
        "## Known vulnerabilities",
        "",
        f"Looked up on [osv.dev](https://osv.dev): all {len(packages)} packages pinned in "
        f"{' and '.join(f'`{lock.file}`' for lock in locks)}, transitive ones included.",
        "",
    ]
    header = [
        "| Package | Version | Advisory | Severity | Fixed in | Summary |",
        "|---|---|---|---|---|---|",
    ]
    rows = advisory_rows(found, sorted(k for k in found if k in shipped), locks)
    lines += ["### Shipped to users", ""]
    if rows:
        lines += [
            "What `ekbatan` and `ekbatan-py-db-scheduler` depend on, with any extra. The lock "
            "file pins what CI tests; a fresh install picks the newest release the ranges allow.",
            "",
            *header,
            *rows,
            "",
        ]
    else:
        lines += ["None in what the published packages depend on.", ""]
    rows = advisory_rows(found, sorted(k for k in found if k not in shipped), locks)
    lines += ["### Tests and tools only", ""]
    if rows:
        count = len([k for k in found if k not in shipped])
        lines += [f"<details><summary>{len(rows)} advisories in {count} packages</summary>", ""]
        lines += header + rows + ["", "</details>", ""]
    else:
        lines += ["None in tests and tools.", ""]

    lines += ["## Newer versions", ""]
    if not updates:
        why = (["under 2 weeks old"] if waiting else []) + (
            ["held back on purpose"] if kept else []
        )
        lines += [
            (
                f"Nothing to take now: every newer release is {' or '.join(why)}, below."
                if why
                else "Every dependency is on its latest release."
            ),
            "",
        ]
    minor = {
        key: entry
        for key, entry in updates.items()
        if not any(is_major(c, entry["latest"]) for c in entry["current"])
    }
    major = {key: entry for key, entry in updates.items() if key not in minor}
    header = ["| Package | Current | Latest | Declared in |", "|---|---|---|---|"]
    for title, entries_ in (
        ("### Patch and minor releases", minor),
        ("### Major releases (may need code changes)", major),
    ):
        rows = []
        for name, entry in sorted(entries_.items()):
            latest = entry["latest"]
            outside = [r for r in sorted(entry["ranges"]) if satisfies(latest, r) is False]
            rows.append(
                f"| `{name}` | {current_cell(entry)} | {latest}"
                + (
                    f" ({entry['young'][0][0]} from {entry['young'][0][1].isoformat()})"
                    if entry.get("young")
                    else ""
                )
                + (f", outside `{outside[0]}`" if outside else "")
                + f" | {list_cell(entry['where'])} |"
            )
        if rows:
            lines += [title, "", *header, *rows, ""]
    if waiting:
        lines += [
            "### Under 2 weeks old",
            "",
            "A release counts once it is 2 weeks old, the wait `exclude-newer` gives uv in "
            "`pyproject.toml`. These don't count yet; each one counts from the date shown, when "
            "its first newer release turns 2 weeks old.",
            "",
            "| Package | Current | Newest | Counts from | Declared in |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| `{name}` | {current_cell(entry)} | {entry['young'][0][0]} "
            f"| {min(day for _, day in entry['young']).isoformat()} | {list_cell(entry['where'])} |"
            for name, entry in sorted(waiting.items())
        ]
        lines += [""]
    if kept:
        lines += [
            "### Held back on purpose",
            "",
            f"<details><summary>{len(kept)} packages, listed in "
            "<code>.github/dependency-check/held-back.txt</code> with the reason</summary>",
            "",
            "| Package | Current | Latest | Why |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| `{name}` | {current_cell(entry)} | {entry['latest']} | {cell(entry['reason'])} |"
            for name, entry in sorted(kept.items())
        ]
        lines += ["", "</details>", ""]

    advisories = sorted(
        {advisory["id"] for advisories in found.values() for advisory in advisories}
    )
    lines.append(f"<!-- vulnerabilities: {' '.join(advisories)} -->")
    state = {
        "vulnerabilities": advisories,
        "updates": len(updates),
        "waiting": len(waiting),
        "clean": bool(packages) and not advisories and not updates,
    }
    return "\n".join(lines) + "\n", state


def snapshot(locks: list[Lock]) -> dict:
    """Every pinned package as a GitHub dependency submission, one manifest per lock file."""

    def env(key: str, default: str) -> str:
        return os.environ.get(key) or default

    sha = (
        env("GITHUB_SHA", "")
        or subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
    )
    repository = env("GITHUB_REPOSITORY", "ekbatan-io/ekbatan-py")
    return {
        "version": 0,
        "sha": sha,
        "ref": env("GITHUB_REF", "refs/heads/main"),
        "job": {
            "correlator": f"{env('GITHUB_WORKFLOW', 'Dependency check')} uv.lock",
            "id": env("GITHUB_RUN_ID", "local"),
        },
        "detector": {
            "name": "ekbatan-py dependency check",
            "version": "1",
            "url": f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{repository}/blob/main/.github/dependency-check/report.py",
        },
        "scanned": datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "manifests": {lock.file: lock.snapshot() for lock in locks},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("report.md"))
    parser.add_argument("--state", type=pathlib.Path, default=pathlib.Path("state.json"))
    parser.add_argument("--snapshot", type=pathlib.Path)
    parser.add_argument(
        "--held-back", type=pathlib.Path, default=pathlib.Path(__file__).with_name("held-back.txt")
    )
    # Release ages count from this day; another date shows what a later run would report.
    parser.add_argument("--today", type=datetime.date.fromisoformat, default=datetime.date.today())
    arguments = parser.parse_args()
    locks = [Lock(ROOT / lock) for lock in LOCKS]
    report, state = write_report(locks, arguments.held_back, arguments.today)
    arguments.out.write_text(report)
    arguments.state.write_text(json.dumps(state, indent=2) + "\n")
    if arguments.snapshot:
        arguments.snapshot.write_text(json.dumps(snapshot(locks), indent=1) + "\n")
    print(
        f"{len(state['vulnerabilities'])} advisories, {state['updates']} newer versions, "
        f"{state['waiting']} waiting"
    )


if __name__ == "__main__":
    main()
