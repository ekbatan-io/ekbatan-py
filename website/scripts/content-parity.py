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
    python3 scripts/content-parity.py
"""

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
    n = norm(head)
    return any(n[:14] in norm(o) or norm(o)[:14] in n for o in ours)


def main() -> int:
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
        rows.append((name, len(jh), len(ph), jw, pw, pct, status, missing))

    print(f"{'page':38} {'J§':>3} {'§':>3} {'§ match':>8} {'words':>6}  status")
    for name, jh, ph, jw, pw, pct, status, note in rows:
        vol = f"{round(100 * pw / jw)}%" if jw and pw else "-"
        print(f"{name:38} {jh:>3} {ph:>3} {str(pct) + '%':>8} {vol:>6}  {status}"
              + (f"  ({note})" if note and status != "done" else ""))

    tally: dict[str, int] = {}
    for r in rows:
        tally[r[6]] = tally.get(r[6], 0) + 1
    settled = tally.get("done", 0) + tally.get("n/a", 0)
    print()
    for k in ("done", "partial", "todo", "n/a"):
        print(f"  {k:9}{tally.get(k, 0):>4}")
    print(f"\n  settled (done + n/a): {settled}/{len(rows)} = {round(100 * settled / len(rows))}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
