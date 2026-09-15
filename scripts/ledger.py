#!/usr/bin/env python3
"""Count `docs/JAVA-PARITY-LEDGER.md`.

The ledger is the port's only honest progress measure: every in-scope Java file read whole and
ported whole. Counting it by hand is how a row quietly stays `partial` for a week, so it is
counted here instead - split by section, because main sources and tests progress separately.

    python scripts/ledger.py                             # the source ledger
    python scripts/ledger.py docs/BEYOND-CODE-LEDGER.md   # everything else
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "docs" / "JAVA-PARITY-LEDGER.md"

#: `| Model.java | 290 | `domain.py` | todo |` - and not a header, a rule, or the scope table.
#:
#: The line count may be `-`, and that case is not decoration: a row with no Java file is a page
#: this port needs and Java does not have. Requiring a digit here dropped every one of them out
#: of the denominator, so `docs/runtime/concurrency.md` could be written and the percentage not
#: move. They count as rows worth 0 lines.
ROW = re.compile(
    r"^\| (?!Java file|file |\*\*|---|`ekbatan|generated)([^|]+)"
    r"\| *(\d+|-) *\| *([^|]*)\| *([\w/]+)"
)
SECTION = re.compile(r"^## (.+)")

STATUSES = ("done", "partial", "todo", "n/a")


def sections(ledger: Path) -> dict[str, list[tuple[str, int, str]]]:
    """Ledger rows, grouped by the `##` heading they fall under."""
    found: dict[str, list[tuple[str, int, str]]] = {}
    current = "(none)"
    for line in ledger.read_text().splitlines():
        heading = SECTION.match(line)
        if heading:
            current = heading.group(1).strip()
            continue
        row = ROW.match(line)
        if row:
            count = 0 if row.group(2) == "-" else int(row.group(2))
            found.setdefault(current, []).append((row.group(1).strip(), count, row.group(4)))
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
    # A second ledger counts the same way, because the discipline is the same: enumerate the
    # files, mark each one, and let a script say how far along it is rather than a feeling.
    ledger = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not ledger.is_absolute():
        ledger = ROOT / ledger
    grouped = sections(ledger)
    if not grouped:
        raise SystemExit(f"no rows matched in {ledger}")
    print(f"\n  {ledger.relative_to(ROOT)}")

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
