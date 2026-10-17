"""F6 side by side: each step one port took against the step the other took in the mirrored
direction - Java's first migration of database A against Python's of database B, and so on.

    python compare_cross.py f6_cross.java.out f6_cross.python.out
"""

import re
import sys

#: (Java's step, Python's step) - the same turn, taken by the other port.
PAIRS = [
    ("a1_java_migrates_v1_v2", "b1_python_migrates_v1_v2"),
    ("b2_java_migrates_v3", "a2_python_migrates_v3"),
    ("a3_java_migrates_v4", "b3_python_migrates_v4"),
    ("a5_java", "a4_python"),
    ("b4_java", "b5_python"),
    ("a5_java_migrates_again", "a4_python_migrates_again"),
    ("b4_java_migrates_again", "b5_python_migrates_again"),
]


def read(path: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.search(r"PROBE ([^:]+): (.*)$", line.rstrip("\n"))
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2)
    return found


java, python = read(sys.argv[1]), read(sys.argv[2])
for d in ("pg", "mysql", "maria"):
    for j, p in PAIRS:
        for suffix in ("outcome", "validate", "history", "tables"):
            jk, pk = f"cross.{d}.{j}.{suffix}", f"cross.{d}.{p}.{suffix}"
            if jk not in java and pk not in python:
                continue
            jv, pv = java.get(jk, "<missing>"), python.get(pk, "<missing>")
            if jv == pv:
                print(f"=    {jk} | {pk}: {jv}")
            else:
                print(f"DIFF {jk} | {pk}\n     java:   {jv}\n     python: {pv}")
    jk, pk = f"cross.{d}.c1_java_outcome", f"cross.{d}.c1_python_outcome"
    print(f"BOTH {d} at once: java {java.get(jk)}, python {python.get(pk)} (one applies, one waits)")
print(f"E pg: python {python.get('cross.pg.e1_python_outcome')}, java {java.get('cross.pg.e1_java_outcome')}")
