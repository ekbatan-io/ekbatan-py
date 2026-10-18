"""Pair each docs/ page with its website copy and print the line diff between them."""
import difflib, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
docs = [p for p in (ROOT / "docs").rglob("*.md")
        if not re.search(r"LEDGER|PARITY|DRACULA|DECISIONS|SHORTCOMINGS|DRIVER|behaviour-parity|parity-fix|parity-round|dracula/|driver-survey", str(p))]
docs += [ROOT / "README.md"]
web = list((ROOT / "website/src/pages").rglob("*.mdx"))

def norm(text, mdx):
    if mdx:
        text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
        text = "\n".join(l for l in text.splitlines() if not l.startswith("import "))
    text = text.replace("—", "-").replace("–", "-")
    return [l.rstrip() for l in text.splitlines() if l.strip()]

W = {w: norm(w.read_text(), True) for w in web}
used = set()
for d in sorted(docs):
    dl = norm(d.read_text(), False)
    best, score = None, 0
    for w, wl in W.items():
        s = len(set(dl) & set(wl)) / max(1, len(set(dl)))
        if s > score:
            best, score = w, s
    rel = d.relative_to(ROOT)
    if score < 0.3:
        print(f"PROBE pair {rel}: NO WEBSITE COPY (best {score:.2f})")
        continue
    used.add(best)
    diff = [l for l in difflib.unified_diff(dl, W[best], lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
    print(f"PROBE pair {rel} <-> {best.relative_to(ROOT)}: overlap {score:.2f}, {len(diff)} differing lines")
    if "-v" in sys.argv:
        for l in diff:
            print("    " + l[:220])
for w in sorted(set(web) - used):
    print(f"PROBE unpaired-website {w.relative_to(ROOT)}")
