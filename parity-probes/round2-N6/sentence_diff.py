"""For each docs page and its website copy, list sentences present in one and absent from the other
(whitespace, links and dashes normalised), so moved or rewrapped text is not counted."""
import re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PAIRS = [l.split(" <-> ") for l in Path(sys.argv[1]).read_text().splitlines() if "<->" in l]

def sents(t, mdx):
    if mdx:
        t = re.sub(r"\A---\n.*?\n---\n", "", t, flags=re.S)
        t = "\n".join(l for l in t.splitlines() if not l.startswith("import "))
    t = t.replace("—", "-").replace("–", "-").replace("…", "...")
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)       # drop link targets
    t = re.sub(r"[#*`>|]", " ", t)
    t = re.sub(r"\s+", " ", t)
    out = set()
    for s in re.split(r"(?<=[.:;!?])\s+", t):
        s = s.strip(" -")
        if len(s) > 25:
            out.add(s)
    return out

for a, b in PAIRS:
    d = a.replace("PROBE pair ", "").strip()
    w = b.split(":")[0].strip()
    ds, ws = sents((ROOT/d).read_text(), False), sents((ROOT/w).read_text(), True)
    only_d, only_w = sorted(ds - ws), sorted(ws - ds)
    print(f"=== {d} <-> {w}: {len(only_d)} only-in-docs, {len(only_w)} only-in-website")
    for s in only_d: print("  D: " + s[:300])
    for s in only_w: print("  W: " + s[:300])
