"""Count how far each page has drifted from its Java counterpart.

`PARITY.md` counts whether a page *exists*. This counts whether it says the same things, by
comparing the rendered text of both built sites - so it measures what a reader sees rather than
what the source looks like.

Two numbers, both mechanical and both proxies:

    section match   the share of Java's headings with a near-match in ours - structural drift
    words           ours as a share of Java's - volume drift

Neither is the goal. They are the cheapest signal for where to look, and a page can sit at 100%
on both and still read differently. Anything genuinely absent for a reason is listed in `NA`
below with that reason, so the number is never improved by pretending.

    cd website && npm run build
    cd ../../ekbatan/website && npm run build
    python3 scripts/content-parity.py              # per page
    python3 scripts/content-parity.py --sections   # per section, within the pages

`--sections` is the one that finds work. A page can sit at 100% on both numbers and still be
missing a third of what Java says under one of its headings, because a page total averages that
away. Run it after the page view goes green; it is where the last 59 gaps came from.
"""

import html as _html
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
JAVA = HERE.parent.parent / "ekbatan" / "website" / "dist"
OURS = HERE / "dist"

#: Java pages with no counterpart here, and why. Each one is a real absence, not a gap.
NA = {
    "learn/native-image": "GraalVM; CPython has no counterpart",
    "reference/runtime/native-image": "GraalVM",
    "reference/annotations": "no annotation processor here",
    "reference/di/micronaut": "no DI container to integrate with",
    "reference/di/quarkus": "no DI container to integrate with",
    "reference/di/spring-boot": "no DI container to integrate with",
    "reference/di/without-di": "is `/reference/wiring/` here",
    "reference/outbox-schema": "a redirect stub for an old Java URL",
}

#: Pages whose remaining gap is fully accounted for, and what accounts for it. A capped page
#: still prints its real numbers - the cap changes how the row is *read*, never what it measures.
#: Every entry names something checkable, so a cap that stops being true is one somebody can
#: disprove. Do not add one to retire a page that still owes content.
CAPPED = {
    "reference/action-plan": (
        "three method headings carry this port's names - add(model) / add_all(models) against "
        "Java's add(entity) / addAll(entities). Renaming them would be a lie about the API"
    ),
    "reference": (
        "seven of Java's cards are for things absent here (five DI, annotations, native image) "
        "and two spell generics Python's way: Model[ID, S], Entity[ID]"
    ),
    "reference/tables/events": (
        "the page is titled for the table as it exists here - `events`, unqualified. Java's is "
        "`eventlog.events`, because its tables live in a schema of their own by default and "
        "these do not"
    ),
    "reference/tables/event-notifications": (
        "same as `reference/tables/events`: the title is the unqualified table name, because "
        "that is where the table is"
    ),
    "learn/complete-project-setup": (
        "Java's StackPicker renders 24 dependency blocks (2 build tools x 4 stacks x 3 dialects) "
        "and a jOOQ codegen section; those two are 47% of its rendered words, and neither has a "
        "counterpart with anything like that surface"
    ),
}

#: Pages whose `CAPPED` reason is about volume, so it accounts for their sections too. A cap
#: about a page *title* does not: `reference/tables/events` is capped for its name and its
#: sections are still scanned.
CAP_COVERS_SECTIONS = {"learn/complete-project-setup"}

#: Sections whose remaining gap is accounted for, keyed by Java's heading. Same rule as
#: `CAPPED`: each reason names something checkable, and none of them retires work still owed.
SECTIONS_OK = {
    "The inTransaction(...) family": "named `tm.transaction()` here; one `with` block, not four overloads",
    "add(entity) / addAll(entities)": "named `add(model)` / `add_all(models)` here",
    "update(entity) / updateAll(entities)": "named `update(model)` / `update_all(models)` here",
    "additions(entityClass) / updates(entityClass)": "named `additions(model_type)` here",
    "Wiring via DI": "no DI container to wire into",
    "Wiring without DI": "the only kind here, so it is just `Wiring`",
    "jOOQ-backed. Thin extension.": "the card reads `Mapped by field name` here",
    "A.1 Add the module": "no module to add; it is an extra and two jobs",
    "Compiling to a native binary": "GraalVM",
    "DDL": "Java repeats the whole CREATE TABLE once per dialect; this has two blocks, not three",
    "SMT error handling": "Java's carries a 1.0.0 upgrade note for its own artifact",
    "How to read the bytes": "same; and the hand-rolled reader it tells you to delete never existed here",
    "@AutoBuilder - generated builders": "no annotation processor; it is `Generated builders` here",
    "@AutoBuilder \u2014 generated builders": "no annotation processor; it is `Generated builders` here",
    "eventlog.events": "the card is named `events`, because that is the table's name here",
    "eventlog.event_notifications": "the card is named `event_notifications`, same reason",
    "Model<T, S>": "spelled `Model[ID, S]`",
    "Entity<T>": "spelled `Entity[ID]`",
    "DI integration": "no DI container",
    "Spring Boot": "no DI container",
    "Quarkus": "no DI container",
    "Micronaut": "no DI container",
    "Plain Java": "is `/reference/wiring/` here",
    "Annotations": "no annotation processor",
    "Native image": "GraalVM",
}

DROP = re.compile(r"<(script|style|svg)\b.*?</\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
HEAD = re.compile(r"<(h[1-3])\b[^>]*>(.*?)</\1>", re.S | re.I)


def page(path: pathlib.Path) -> tuple[list[str], int]:
    """Its headings, and how many words it holds."""
    html = DROP.sub(" ", path.read_text(errors="replace"))
    heads = [TAG.sub("", m.group(2)) for m in HEAD.finditer(html)]
    heads = [re.sub(r"\s+", " ", h).strip() for h in heads]
    words = len(TAG.sub(" ", html).split())
    return [h for h in heads if h], words


def route(root: pathlib.Path, path: pathlib.Path) -> str:
    return str(path.parent.relative_to(root)).replace(".", "") or "/"


def norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower())


def matches(head: str, ours: list[str]) -> bool:
    """Whether one of our headings is Java's, allowing for a rename around the same words.

    Anchored at the front rather than anywhere in the string. A free substring test counted
    `Five backends` as matched by `Reentrancy contract - uniform across all five backends`,
    which is how a section can go missing while the page reports 100%.
    """
    n = norm(head)
    return any(
        norm(o) == n or norm(o).startswith(n[:14]) or n.startswith(norm(o)[:14]) for o in ours
    )


def sections(path: pathlib.Path) -> list[tuple[str, int]]:
    """Each heading and every word beneath it, subsections included.

    Counting only as far as the next heading of any level reports 0 for a `## DDL` whose body
    all sits under `### PostgreSQL`, and then calls that section missing.
    """
    html = DROP.sub(" ", path.read_text(errors="replace"))
    parts = re.split(r"<h([23])\b[^>]*>(.*?)</h\1>", html, flags=re.S | re.I)
    heads = []
    for i in range(1, len(parts), 3):
        title = _html.unescape(re.sub(r"\s+", " ", TAG.sub("", parts[i + 1])).strip())
        own = len(TAG.sub(" ", parts[i + 2]).split()) if i + 2 < len(parts) else 0
        heads.append((int(parts[i]), title, own))

    out = []
    for n, (level, title, own) in enumerate(heads):
        total = own
        for deeper, _, deeper_own in heads[n + 1 :]:
            if deeper <= level:
                break
            total += deeper_own
        out.append((title, total))
    return out


def thin() -> int:
    """Every section materially shorter than Java's, worst first."""
    rows = []
    for jf in sorted(JAVA.rglob("index.html")):
        name = route(JAVA, jf)
        if name.startswith("concepts") or name in NA or name in CAP_COVERS_SECTIONS:
            continue
        pf = OURS / (name if name != "/" else "") / "index.html"
        if not pf.exists():
            continue
        ours = sections(pf)
        for title, jw in sections(jf):
            n = norm(title)
            exact = [w for t2, w in ours if norm(t2) == n]
            loose = [
                w
                for t2, w in ours
                if norm(t2).startswith(n[:14]) or n.startswith(norm(t2)[:14])
            ]
            mine = exact[0] if exact else (loose[0] if loose else None)
            if mine is None or (jw > 90 and mine < jw * 0.6):
                rows.append((jw - (mine or 0), name, title, jw, mine))

    rows.sort(reverse=True)
    owed = [r for r in rows if r[2] not in SECTIONS_OK]
    print(f"{'page':36} {'section':44} {'J':>5} {'ours':>5}")
    for _, name, title, jw, mine in rows:
        note = SECTIONS_OK.get(title)
        print(
            f"{name[:35]:36} {title[:43]:44} {jw:>5} {str(mine):>5}"
            + (f"  ok: {note}" if note else "")
        )
    print(f"\n  {len(rows)} thin or missing, {len(rows) - len(owed)} accounted for, {len(owed)} owed")
    return 1 if owed else 0


def main() -> int:
    if "--sections" in sys.argv:
        if not JAVA.exists():
            sys.exit(f"build the Java site first: {JAVA} does not exist")
        return thin()
    if not JAVA.exists():
        sys.exit(f"build the Java site first: {JAVA} does not exist")
    rows = []
    for jf in sorted(JAVA.rglob("index.html")):
        name = route(JAVA, jf)
        if name.startswith("concepts"):
            continue  # five-line redirects, nothing to compare
        jh, jw = page(jf)
        if name in NA:
            rows.append((name, len(jh), 0, jw, 0, 0, "n/a", NA[name]))
            continue
        pf = OURS / (name if name != "/" else "") / "index.html"
        if not pf.exists():
            rows.append((name, len(jh), 0, jw, 0, 0, "todo", "no counterpart"))
            continue
        ph, pw = page(pf)
        pct = round(100 * sum(matches(h, ph) for h in jh) / max(1, len(jh)))
        vol = round(100 * pw / jw) if jw else 0
        status = "done" if pct >= 90 and vol >= 75 else "partial" if pct >= 50 else "todo"
        missing = ", ".join(h for h in jh if not matches(h, ph))[:70]
        if status != "done" and name in CAPPED:
            status, missing = "capped", CAPPED[name]
        rows.append((name, len(jh), len(ph), jw, pw, pct, status, missing))

    print(f"{'page':38} {'J§':>3} {'§':>3} {'§ match':>8} {'words':>6}  status")
    for name, jh, ph, jw, pw, pct, status, note in rows:
        vol = f"{round(100 * pw / jw)}%" if jw and pw else "-"
        print(f"{name:38} {jh:>3} {ph:>3} {str(pct) + '%':>8} {vol:>6}  {status}"
              + (f"  ({note})" if note and status != "done" else ""))

    tally: dict[str, int] = {}
    for r in rows:
        tally[r[6]] = tally.get(r[6], 0) + 1
    settled = tally.get("done", 0) + tally.get("capped", 0) + tally.get("n/a", 0)
    print()
    for k in ("done", "capped", "partial", "todo", "n/a"):
        print(f"  {k:9}{tally.get(k, 0):>4}")
    print(f"\n  settled: {settled}/{len(rows)} = {round(100 * settled / len(rows))}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
