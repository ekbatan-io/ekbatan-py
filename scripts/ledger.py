#!/usr/bin/env python3
"""Count `docs/JAVA-PARITY-LEDGER.md`.

The ledger is the port's only honest progress measure: every in-scope Java file read whole and
ported whole. Counting it by hand is how a row quietly stays `partial` for a week, so it is
counted here instead - split by section, because main sources and tests progress separately.

    python scripts/ledger.py
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "docs" / "JAVA-PARITY-LEDGER.md"

#: `| Model.java | 290 | `domain.py` | todo |` - and not a header, a rule, or the scope table.
ROW = re.compile(r"^\| (?!Java file|file |\*\*|---|`ekbatan|generated)([^|]+)\| *(\d+) *\| *([^|]*)\| *([\w/]+)")
SECTION = re.compile(r"^## (.+)")

STATUSES = ("done", "partial", "todo", "n/a")


def sections() -> dict[str, list[tuple[str, int, str]]]:
    """Ledger rows, grouped by the `##` heading they fall under."""
    found: dict[str, list[tuple[str, int, str]]] = {}
    current = "(none)"
    for line in LEDGER.read_text().splitlines():
        heading = SECTION.match(line)
        if heading:
            current = heading.group(1).strip()
            continue
        row = ROW.match(line)
        if row:
            found.setdefault(current, []).append(
                (row.group(1).strip(), int(row.group(2)), row.group(4))
            )
    return found


def report(title: str, rows: list[tuple[str, int, str]]) -> tuple[int, int]:
    files = Counter(status for _, _, status in rows)
    lines: Counter[str] = Counter()
    for _, count, status in rows:
        lines[status] += count

    unknown = set(files) - set(STATUSES)
    if unknown:
        raise SystemExit(f"unknown status in {title}: {sorted(unknown)}")

    print(f"\n  {title}")
    print(f"  {'':11}{'files':>6}{'lines':>8}")
    for status in STATUSES:
        print(f"  {status:11}{files[status]:>6}{lines[status]:>8}")
    print(f"  {'TOTAL':11}{len(rows):>6}{sum(lines.values()):>8}")
    return files["done"] + files["n/a"], len(rows)


def main() -> None:
    grouped = sections()
    if not grouped:
        raise SystemExit(f"no rows matched in {LEDGER}")

    settled = total = 0
    for title, rows in grouped.items():
        done, count = report(title, rows)
        settled += done
        total += count

    print(f"\n  settled (done + n/a): {settled}/{total} = {100 * settled / total:.0f}%")
    still = [name for rows in grouped.values() for name, _, status in rows if status == "partial"]
    print(f"  partial: {', '.join(still) if still else 'none'}")


if __name__ == "__main__":
    main()
