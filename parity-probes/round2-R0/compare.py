"""Three-way compare of PROBE lines: Java (reference), round-1 Python, today's Python.

    uv run --no-sync python parity-probes/round2-R0/compare.py <java.out> <r1.python.out> <now.out> [prefix-filter]

Prints counts and every key in the classes fixed / regressed / still-differ / new-differ.
"""

import sys


def load(path, flt=None):
    d = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if not line.startswith("PROBE "):
            continue
        k, _, v = line[6:].partition(": ")
        if flt and not k.startswith(flt):
            continue
        d[k] = v
    return d


j, r1, now = (load(p, sys.argv[4] if len(sys.argv) > 4 else None) for p in sys.argv[1:4])
keys = list(dict.fromkeys([*j, *r1, *now]))
M = "<missing>"
cls = {"same": [], "fixed": [], "regressed": [], "still": [], "changed-still": [], "py-only": []}
for k in keys:
    jv, rv, nv = j.get(k, M), r1.get(k, M), now.get(k, M)
    if jv == M:
        cls["py-only"].append((k, jv, rv, nv))
        continue
    r_ok, n_ok = rv == jv, nv == jv
    if r_ok and n_ok:
        cls["same"].append((k, jv, rv, nv))
    elif not r_ok and n_ok:
        cls["fixed"].append((k, jv, rv, nv))
    elif r_ok and not n_ok:
        cls["regressed"].append((k, jv, rv, nv))
    elif rv == nv:
        cls["still"].append((k, jv, rv, nv))
    else:
        cls["changed-still"].append((k, jv, rv, nv))
print("COUNTS " + " ".join(f"{c}={len(v)}" for c, v in cls.items()))
for c in ("regressed", "changed-still", "fixed", "still", "py-only"):
    for k, jv, rv, nv in cls[c]:
        print(f"[{c}] {k}\n    java: {jv}\n    r1  : {rv}\n    now : {nv}")
