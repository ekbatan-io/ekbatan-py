"""Check that every relative link in the documentation points at something.

A docs tree is written page by page and cross-linked to pages that do not exist yet, which is
fine while it is being written and is a broken promise once it ships. This makes that countable
instead of hopeful - the same reason the ledgers exist.

    uv run python scripts/links.py

Only relative links are checked. An http(s) link is somebody else's uptime problem.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: `[text](target)`, skipping images and anything already absolute.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")

#: Markdown's own anchor rules, near enough: lowercase, spaces to hyphens, drop the rest.
PUNCTUATION = re.compile(r"[^\w\- ]")


def anchors(text: str) -> set[str]:
    found = set()
    for line in text.splitlines():
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            found.add(PUNCTUATION.sub("", heading).lower().replace(" ", "-"))
    return found


def main() -> int:
    skip = {".venv", "node_modules", ".git"}
    files = sorted(p for p in ROOT.rglob("*.md") if not skip & set(p.parts))
    broken: list[str] = []
    checked = 0

    for path in files:
        text = path.read_text()
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            checked += 1

            if not file_part:  # same-page anchor
                if anchor and anchor not in anchors(text):
                    broken.append(f"{path.relative_to(ROOT)}: #{anchor} - no such heading")
                continue

            resolved = (path.parent / file_part).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)}: {target} - no such file")
            elif anchor and resolved.is_file() and anchor not in anchors(resolved.read_text()):
                broken.append(f"{path.relative_to(ROOT)}: {target} - no such heading")

    print(f"{checked} relative links in {len(files)} files")
    for line in broken:
        print(f"  BROKEN  {line}")
    print(f"{len(broken)} broken" if broken else "all resolve")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
