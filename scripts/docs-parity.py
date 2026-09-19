"""How far each `docs/` page has drifted from the Java port's.

`website/CONTENT-PARITY.md` does this for the rendered site. This is the same question for the
Markdown tree, and it needs no build: Java's docs sit next door and both sides are plain files.

    python scripts/docs-parity.py              # per page
    python scripts/docs-parity.py --sections   # per section, within the pages

Two numbers, both proxies:

    section match   the share of Java's headings with a near-match here - structural drift
    words           ours as a share of Java's - volume drift

Neither is the goal, and the page view goes green well before the pages agree - run
`--sections` after it does. Every page and every section ends accounted for: `NA` for what this
port should not have, `CAPPED` and `SECTIONS_OK` for a gap somebody has looked at and can
defend. Each reason names something checkable, so one that stops being true can be disproved.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
JAVA = ROOT.parent / "ekbatan" / "docs"
OURS = ROOT / "docs"

#: Java pages this port keeps under a different name.
MAP = {
    "database/jooq-codegen.md": "database/schemagen.md",
    "wiring/without-di.md": "wiring.md",
}

#: Java pages with no counterpart here, and why. Each is a real absence, not a gap.
NA = {
    "runtime/native-image.md": "GraalVM; CPython has no counterpart",
    "wiring/annotations.md": "no annotation processor here",
    "wiring/micronaut.md": "no DI container to integrate with",
    "wiring/quarkus.md": "no DI container to integrate with",
    "wiring/spring.md": "no DI container to integrate with",
    "wiring/README.md": "one wiring page here, so there is no section to index",
    "gradle/README.md": "no Gradle",
    "gradle/getting-started.md": "no Gradle",
    "gradle/jooq-codegen.md": "no Gradle, no jOOQ",
    "maven/README.md": "no Maven",
    "maven/getting-started.md": "no Maven",
    "maven/jooq-codegen.md": "no Maven, no jOOQ",
    "database/outbox-schema.md": "a stub redirecting an old URL",
    "medium/01-event-driven-persistence-with-ekbatan.md": "an article, not reference",
}

#: Pages whose remaining gap is accounted for. The row still prints its real numbers.
CAPPED = {
    "database/jooq-codegen.md": (
        "jOOQ generates code from a schema; `schemagen` reflects a migrated database into "
        "`Table` objects. Same job, and almost no shared surface to compare"
    ),
    "database/postgresql.md": (
        "Java inlines the full DDL of all three framework tables on each of its three dialect "
        "pages. That is the same 400 lines three times; here each page says what the tables "
        "look like on that dialect and links to the one place the DDL lives"
    ),
    "database/mysql.md": "same as `database/postgresql.md`",
    "database/mariadb.md": "same as `database/postgresql.md`",
    "wiring/without-di.md": (
        "`The full wiring` and `What each block does` are one numbered walkthrough here, and "
        "`Same thing with less code` says to use a DI container, which is the thing this page "
        "exists because there is not one"
    ),
    "concepts/actions.md": "the per-call state is a `ContextVar`, not a `ScopedValue`",
    "concepts/models-and-entities.md": "no annotation processor, so no `@AutoBuilder`",
    "database/transaction-manager.md": (
        "named `tm.transaction()` here - one `with` block where Java needs four overloads"
    ),
    "database/tables/event-notifications.md": (
        "one `MySQL and MariaDB` block, because the generated DDL is identical on both"
    ),
    "database/tables/scheduled-tasks.md": "same as `database/tables/event-notifications.md`",
}

#: Sections whose remaining gap is accounted for, keyed by Java's heading.
SECTIONS_OK = {
    "The inTransaction(...) family": "named `tm.transaction()` here",
    "@AutoBuilder - generated builders": "no annotation processor; `Generated builders` here",
    "@AutoBuilder — generated builders": "no annotation processor; `Generated builders` here",
    "Singletons + per-call state via ScopedValue": "a `ContextVar` here",
    "The domain (unchanged whether you use DI or not)": "phrased `whether you wire it by hand`",
    "The full wiring": "the numbered walkthrough is this, and `What each block does`",
    "What each block does": "each numbered step explains itself",
    "Same thing with less code": "says to use a DI container; there is not one here",
    "Framework tables": "linked rather than inlined three times",
    "eventlog.events (always required)": "linked rather than inlined per dialect",
    "eventlog.event_notifications (only with ekbatan-events-local-event-handler)": "linked",
    (
        "eventlog.event_notifications + partial index "
        "(only with ekbatan-events-local-event-handler)"
    ): "linked",
    "scheduled_tasks (only with ekbatan-distributed-jobs)": "linked",
    "V0000__create_eventlog_database.sql": "the revision is named in `Migration order`",
    "mysql_init.sql": "shown under `Migration order`",
    "mariadb_init.sql": "shown under `Migration order`",
    "jOOQ codegen": "no jOOQ; `schemagen` is the counterpart",
    "MariaDB": "one `MySQL and MariaDB` block; the generated DDL is identical",
    "MySQL": "one `MySQL and MariaDB` block; the generated DDL is identical",
    "Domain tables": "on the dialect pages, which is where Java has it too",
    "How to read the bytes": "Java's carries a 1.0.0 upgrade note for its own artifact",
    "SMT error handling": "same; the note is about a jar this port does not build",
}

CODE = re.compile(r"```.*?```", re.S)


def heads(text: str) -> list[str]:
    return [
        re.sub(r"[`*]", "", line.lstrip("#").strip())
        for line in CODE.sub("", text).splitlines()
        if re.match(r"^#{2,3} ", line)
    ]


def sections(text: str) -> list[tuple[str, int]]:
    """Each heading and every word beneath it, subsections included."""
    parts = re.split(r"^(#{2,3}) (.*)$", text, flags=re.M)
    found = [
        (
            len(parts[i]),
            re.sub(r"[`*]", "", parts[i + 1]).strip(),
            len(parts[i + 2].split()) if i + 2 < len(parts) else 0,
        )
        for i in range(1, len(parts), 3)
    ]
    out = []
    for n, (level, title, own) in enumerate(found):
        total = own
        for deeper, _, deeper_own in found[n + 1 :]:
            if deeper <= level:
                break
            total += deeper_own
        out.append((title, total))
    return out


def norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower())


def matches(head: str, ours: list[str]) -> bool:
    """Anchored at the front: a free substring test calls a missing section matched."""
    n = norm(head)
    return any(
        norm(o) == n or norm(o).startswith(n[:14]) or n.startswith(norm(o)[:14]) for o in ours
    )


def counterpart(name: str) -> pathlib.Path:
    return OURS / MAP.get(name, name)


def pages() -> int:
    rows = []
    for jf in sorted(JAVA.rglob("*.md")):
        name = str(jf.relative_to(JAVA))
        if name in NA:
            rows.append((name, 0, 0, 0, 0, 0, "n/a", NA[name]))
            continue
        pf = counterpart(name)
        jt = jf.read_text()
        if not pf.exists():
            rows.append((name, len(heads(jt)), 0, len(jt.split()), 0, 0, "todo", "no counterpart"))
            continue
        pt = pf.read_text()
        jh, ph = heads(jt), heads(pt)
        jw, pw = len(jt.split()), len(pt.split())
        vol = round(100 * pw / jw) if jw else 0
        # An index page has no `##` at all; volume is the only signal it has.
        pct = (
            round(100 * sum(matches(h, ph) for h in jh) / len(jh))
            if jh
            else (100 if vol >= 75 else 0)
        )
        status = "done" if pct >= 90 and vol >= 75 else "partial" if pct >= 50 else "todo"
        note = ", ".join(h for h in jh if not matches(h, ph))[:60]
        if status != "done" and name in CAPPED:
            status, note = "capped", CAPPED[name]
        rows.append((name, len(jh), len(ph), jw, pw, pct, status, note))

    print(f"{'java page':44} {'J§':>3} {'§':>3} {'§ match':>8} {'words':>6}  status")
    for name, jh, ph, jw, pw, pct, status, note in rows:
        vol = f"{round(100 * pw / jw)}%" if jw and pw else "-"
        print(
            f"{name:44} {jh:>3} {ph:>3} {str(pct) + '%':>8} {vol:>6}  {status}"
            + (f"  ({note})" if note and status != "done" else "")
        )

    tally: dict[str, int] = {}
    for r in rows:
        tally[r[6]] = tally.get(r[6], 0) + 1
    settled = tally.get("done", 0) + tally.get("capped", 0) + tally.get("n/a", 0)
    print()
    for key in ("done", "capped", "partial", "todo", "n/a"):
        print(f"  {key:9}{tally.get(key, 0):>4}")
    print(f"\n  settled: {settled}/{len(rows)} = {round(100 * settled / len(rows))}%")
    return 0


def thin() -> int:
    rows = []
    for jf in sorted(JAVA.rglob("*.md")):
        name = str(jf.relative_to(JAVA))
        if name in NA or name in CAPPED:
            continue
        pf = counterpart(name)
        if not pf.exists():
            continue
        ours = sections(pf.read_text())
        for title, jw in sections(jf.read_text()):
            n = norm(title)
            exact = [w for t2, w in ours if norm(t2) == n]
            loose = [
                w for t2, w in ours if norm(t2).startswith(n[:14]) or n.startswith(norm(t2)[:14])
            ]
            mine = exact[0] if exact else (loose[0] if loose else None)
            if mine is None or (jw > 90 and mine < jw * 0.6):
                rows.append((jw - (mine or 0), name, title, jw, mine))

    rows.sort(reverse=True)
    owed = [r for r in rows if r[2] not in SECTIONS_OK]
    print(f"{'java page':38} {'section':44} {'J':>5} {'ours':>5}")
    for _, name, title, jw, mine in rows:
        note = SECTIONS_OK.get(title)
        print(
            f"{name[:37]:38} {title[:43]:44} {jw:>5} {mine!s:>5}"
            + (f"  ok: {note}" if note else "")
        )
    print(
        f"\n  {len(rows)} thin or missing, {len(rows) - len(owed)} accounted for, {len(owed)} owed"
    )
    return 1 if owed else 0


def main() -> int:
    if not JAVA.exists():
        sys.exit(f"the Java docs are not next door: {JAVA} does not exist")
    return thin() if "--sections" in sys.argv else pages()


if __name__ == "__main__":
    sys.exit(main())
