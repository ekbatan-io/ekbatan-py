"""Every divergence must say whether anybody chose it.

`DIVERGENCE.md` explains *what* differs and *why*. What it did not say is **who decided** - so a
judgement call sat in it for weeks reading exactly like a fact about the language, and was found
by luck rather than by review. `readonly_db` meaning "a read" instead of "the replica" was that
one, and it changed which database a call went to.

So every section now carries a verdict on the line after its heading, and this refuses to pass
without one:

    > **forced** - reason.        CPython left no choice. Nothing to review.
    > **chosen** - reason.        Somebody decided. Must link DECISIONS-TO-REVIEW.md.
    > **parity** - reason.        Recorded here, but the two ports agree.

    uv run python scripts/divergence.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERDICTS = ("forced", "chosen", "parity")
#: Headings that explain the file rather than record a divergence.
NOT_A_DIVERGENCE = {"How to read this"}
#: `> **chosen** - because ...`, on the line after a `## ` heading.
LABEL = re.compile(r"^> \*\*(" + "|".join(VERDICTS) + r")\*\* - (.+)$")


def sections(text: str) -> list[tuple[str, str | None, str]]:
    """Each `## ` heading, its verdict if it has one, and the body under it."""
    found = []
    for block in re.split(r"\n(?=## )", text)[1:]:
        lines = block.split("\n")
        # The first non-blank line under the heading, so the label may be spaced off it.
        rest = next((line.strip() for line in lines[1:] if line.strip()), "")
        label = LABEL.match(rest)
        heading = lines[0][3:].strip()
        if heading in NOT_A_DIVERGENCE:
            continue
        found.append((heading, label.group(1) if label else None, block))
    return found


def main() -> int:
    text = (ROOT / "DIVERGENCE.md").read_text()
    entries = sections(text)

    unlabelled = [name for name, verdict, _ in entries if verdict is None]
    # A chosen divergence is one somebody could have decided differently, so it has to be
    # somewhere it will actually be argued about - not just described.
    unreviewed = [
        name
        for name, verdict, body in entries
        if verdict == "chosen" and "DECISIONS-TO-REVIEW" not in body
    ]

    tally = Counter(verdict for _, verdict, _ in entries if verdict)
    print(f"  {len(entries)} divergences")
    for verdict in VERDICTS:
        print(f"    {verdict:8}{tally[verdict]:>4}")

    for name in unlabelled:
        print(f"  UNLABELLED  {name}")
    for name in unreviewed:
        print(f"  UNREVIEWED  {name} - marked chosen but links no decision")

    if unlabelled or unreviewed:
        print(f"\n  {len(unlabelled)} unlabelled, {len(unreviewed)} chosen-but-unreviewed")
        return 1
    print("\n  every divergence is triaged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
