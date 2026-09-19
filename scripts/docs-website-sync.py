"""The website must not become a second source of truth.

`docs/` is the canonical Markdown - what a reader sees on GitHub and in an editor. The website
renders the same knowledge for the web. The failure this catches is one-directional and quiet:
prose written straight into an `.mdx` page, never carried across, until `docs/` describes a
framework that no longer matches the one the website describes.

It happened. Six pages drifted while the website was being brought level with the Java port's;
`docs/events/event-streaming.md` held 19% of what its page said, missing the entire transform
manual. Word count is a crude proxy for that and a good enough one: a page cannot lose a third
of its words to formatting.

    uv run python scripts/docs-website-sync.py

Pairs are listed rather than inferred, because the two trees are organised differently on
purpose - `reference/multi-database` is `database/multi-database`, and `reference/model` and
`reference/entity` are both `concepts/models-and-entities`.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "website" / "src" / "pages"
DOCS = ROOT / "docs"

#: The floor a page may not drop below, as a share of its website counterpart's words. Not 100:
#: the website carries components, a dialect picker and pagers that have no Markdown form, and
#: a docs page is free to be tighter. Below this, something was written in one place only.
FLOOR = 80

#: Where two pages share one docs file, the docs file is compared against the longer of them.
PAIRS: dict[str, tuple[str, ...]] = {
    "concepts/actions.md": ("reference/action.mdx", "reference/action-plan.mdx"),
    "concepts/models-and-entities.md": ("reference/model.mdx", "reference/entity.mdx"),
    "database/keyed-locks.md": ("reference/keyed-locks.mdx",),
    "database/migrations.md": ("reference/migrations.mdx",),
    "database/multi-database.md": ("reference/multi-database.mdx",),
    "database/repositories.md": ("reference/repository.mdx",),
    "database/schemagen.md": ("reference/schemagen.mdx",),
    "database/sharding.md": ("reference/sharding.mdx",),
    "database/tables/event-notifications.md": ("reference/tables/event-notifications.mdx",),
    "database/tables/events.md": ("reference/tables/events.mdx",),
    "database/tables/scheduled-tasks.md": ("reference/tables/scheduled-tasks.mdx",),
    "database/transaction-manager.md": ("reference/transaction-manager.mdx",),
    "events/event-streaming.md": ("reference/events/streaming.mdx",),
    "events/local-event-handler.md": ("reference/events/local-event-handler.mdx",),
    "jobs/distributed-jobs.md": ("reference/jobs/distributed-jobs.mdx",),
    "runtime/concurrency.md": ("reference/runtime/concurrency.mdx",),
    "runtime/observability.md": ("reference/runtime/observability.mdx",),
    "wiring-fastapi.md": ("reference/wiring-fastapi.mdx",),
    "wiring.md": ("reference/wiring.mdx",),
}


def words(path: pathlib.Path) -> int:
    return len(path.read_text().split()) if path.exists() else 0


def main() -> int:
    behind: list[str] = []
    missing: list[str] = []
    rows: list[tuple[int, str, int, int]] = []

    for doc, pages in sorted(PAIRS.items()):
        doc_path = DOCS / doc
        if not doc_path.exists():
            missing.append(f"{doc} - no such file, for {', '.join(pages)}")
            continue
        web = max(words(WEB / p) for p in pages)
        if web == 0:
            missing.append(f"{', '.join(pages)} - no such page, for {doc}")
            continue
        share = round(100 * words(doc_path) / web)
        rows.append((share, doc, words(doc_path), web))
        if share < FLOOR:
            behind.append(f"{doc} holds {share}% of {max(pages, key=lambda p: words(WEB / p))}")

    for share, doc, dw, web in sorted(rows):
        print(f"  {doc:42} {dw:>6} vs {web:>6}  {share:>3}%")
    print(f"\n{len(rows)} pairs, {len(behind)} below {FLOOR}%, {len(missing)} unpaired")
    for line in behind:
        print(f"  BEHIND   {line}")
    for line in missing:
        print(f"  MISSING  {line}")
    return 1 if behind or missing else 0


if __name__ == "__main__":
    sys.exit(main())
