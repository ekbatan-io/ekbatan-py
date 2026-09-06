#!/usr/bin/env python3
"""Count `docs/FILE-LEDGER.md`.

The ledger is the port's only honest progress measure: 159 Java source files in, each read
whole and ported whole. Counting it by hand is how a row quietly stays `partial` for a week, so
it is counted here instead.

    python scripts/ledger.py
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "docs" / "FILE-LEDGER.md"

#: `| Name | 97 | task/__init__.py | done |` - and not the header or the section rules.
ROW = re.compile(r"^\| (?!Java file|\*\*|---)([^|]+)\| *(\d+) *\| *([^|]*)\| *([\w/]+)")

STATUSES = ("done", "partial", "todo", "n/a")


def rows() -> list[tuple[str, int, str]]:
    found = []
    for line in LEDGER.read_text().splitlines():
        match = ROW.match(line)
        if match:
            found.append((match.group(1).strip(), int(match.group(2)), match.group(4)))
    return found


def main() -> None:
    counted = rows()
    files = Counter(status for _, _, status in counted)
    lines = Counter()
    for _, count, status in counted:
        lines[status] += count

    unknown = set(files) - set(STATUSES)
    if unknown:
        raise SystemExit(f"unknown status in the ledger: {sorted(unknown)}")

    print(f"  {'':9}{'files':>6}{'lines':>8}")
    for status in STATUSES:
        print(f"  {status:9}{files[status]:>6}{lines[status]:>8}")
    print(f"  {'TOTAL':9}{len(counted):>6}{sum(lines.values()):>8}")

    settled = files["done"] + files["n/a"]
    print(f"\n  settled (done + n/a): {settled}/{len(counted)} = {100 * settled / len(counted):.0f}%")
    still = [name for name, _, status in counted if status == "partial"]
    print(f"  partial: {', '.join(still) if still else 'none'}")


if __name__ == "__main__":
    main()
