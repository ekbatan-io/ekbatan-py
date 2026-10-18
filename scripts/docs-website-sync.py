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
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "website" / "src" / "pages"
DOCS = ROOT / "docs"

#: The floor a page may not drop below, as a share of its website counterpart's words. Not 100:
#: the website carries components, a dialect picker and pagers that have no Markdown form, and
#: a docs page is free to be tighter. Below this, something was written in one place only.
FLOOR = 80

#: Website sections with no docs counterpart by design. A reference page carries a type
#: signature and a method list that a concepts page has no business repeating, and the site
#: carries navigation a Markdown tree does not have.
SECTION_OK = {
    "Type signature",
    "Methods",
    "Declaring an action",
    "Lifecycle",
    "Construction and mutation",
    "Equality",
    "Builder API",
    "Differences from Model",
    "Why frozen",
    "See also",
    "When to use Entity rather than Model",
    "Suggested next steps",
    "Next",
    "plan",
    "clock",
    "perform(principal, params)",
    "Cross-shard execution",
    "Retry semantics",
    "Actions are singletons",
    "No nesting",
    "add(model) / add_all(models)",
    "update(model) / update_all(models)",
    "additions(model_type) / updates(model_type)",
    "changes()",
    "Single-writer; not thread-safe",
    "What just happened",
    "Test it without a database",
    # A tutorial and a concepts page answer the same question under different titles. The docs
    # tree follows the Java port's naming, which is the one a porter cross-checks against.
    "What happens after commit",
    "One trap worth knowing",
    # Java's website says `Events` and Java's docs say `ModelEvent`; each side here follows
    # its own counterpart, so this pair disagrees on purpose.
    "Events",
}

#: Where two pages share one docs file, the docs file is compared against the longer of them.
PAIRS: dict[str, tuple[str, ...]] = {
    "concepts/actions.md": (
        "reference/action.mdx",
        "reference/action-plan.mdx",
        "learn/actions.mdx",
    ),
    "concepts/models-and-entities.md": (
        "reference/model.mdx",
        "reference/entity.mdx",
        "learn/models-and-entities.mdx",
    ),
    "concepts/outbox.md": ("learn/outbox.mdx",),
    "concepts/sagas.md": ("learn/sagas.mdx",),
    "concepts/sharding.md": ("learn/sharding-strategies.mdx",),
    "concepts/the-dual-write-trap.md": ("learn/the-dual-write-trap.mdx",),
    "database/keyed-locks.md": ("reference/keyed-locks.mdx",),
    "database/mariadb.md": ("reference/dialects/mariadb.mdx",),
    "database/mysql.md": ("reference/dialects/mysql.mdx",),
    "database/postgresql.md": ("reference/dialects/postgresql.mdx",),
    "database/sqlite.md": ("reference/dialects/sqlite.mdx",),
    "database/tables.md": ("reference/tables/index.mdx",),
    "database/migrations.md": ("reference/migrations.mdx",),
    "database/multi-database.md": ("reference/multi-database.mdx",),
    "database/repositories.md": ("reference/repository.mdx",),
    "beside-java.md": ("reference/beside-java.mdx",),
    "database/config-files.md": ("reference/config-files.mdx",),
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


def headings(path: pathlib.Path) -> list[str]:
    text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
    return [
        re.sub(r"[`*]", "", line.lstrip("#").strip())
        for line in text.splitlines()
        if re.match(r"^#{2,3} ", line)
    ]


def norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower())


def covered(head: str, ours: list[str]) -> bool:
    n = norm(head)
    return any(
        norm(o) == n or norm(o).startswith(n[:14]) or n.startswith(norm(o)[:14]) for o in ours
    )


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

        # Volume is a page average and hides a whole section. A heading the website has and
        # the docs page does not is the shape the drift actually took.
        mine = headings(doc_path)
        for page in pages:
            src = WEB / page
            if not src.exists():
                continue
            for head in headings(src):
                if head not in SECTION_OK and not covered(head, mine):
                    behind.append(f"{doc} has no section for `{head}` ({page})")

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
