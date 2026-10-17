"""Side by side: every PROBE key, with Java's and Python's value, and = or DIFF.

    python compare.py java.out python.out
"""

import re
import sys


def read(path: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.search(r"PROBE ([^:]+): (.*)$", line.rstrip("\n"))
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2)
    return found


java, python = read(sys.argv[1]), read(sys.argv[2])
for key in sorted(set(java) | set(python)):
    j, p = java.get(key, "<missing>"), python.get(key, "<missing>")
    mark = "=" if j == p else "DIFF"
    if mark == "=":
        print(f"=    {key}: {j}")
    else:
        print(f"DIFF {key}\n     java:   {j}\n     python: {p}")
