# Ledger: sync and async as a first-class choice in the docs

Asked on 2026-10-19: make Sync | Async a deliberate choice across the setup guide, the docs, the
website and AGENTS.md. Judgement calls: `docs/DECISIONS-TO-REVIEW.md#syncasync-docs`.

## Steps

| # | Step | State |
|---|---|---|
| 1 | Read AGENTS.md, the setup guide, the pickers, the checker, sync-examples, the async docs and examples | done |
| 2 | Async SQLite example `wallet-service-{uv,pip,poetry}-async-sqlite`; sync-examples, Makefile, mypy, READMEs, docs lists | pending |
| 3 | Setup guide: a Mode row in the picker; every step that differs shows the async example's files; SQLite gets real steps; step 19 removed | pending |
| 4 | `scripts/check-setup-guide.py` over db x installer x mode (24) | pending |
| 5 | `ModeTabs` component; every website code sample that differs by mode gets a Sync / Async switch | pending |
| 6 | docs/ copies: the two blocks one after another under Sync / Async labels | pending |
| 7 | "Sync or async?" page, docs + website, linked from nav, learn and the setup guide; async reference trimmed; going-async kept or merged | pending |
| 8 | AGENTS.md "Two modes" section; stale text updated | pending |
| 9 | Verify: make lint, npm build + check, headless browser, checker 24, examples, unit tests | pending |

## Page inventory (step 5-6)

Every website page with Python code, and whether a sample differs by mode. Filled as each page is
read whole.

| page | docs copy | samples that differ | state |
|---|---|---|---|

## Checker results (step 4)

| db | installer | sync | async |
|---|---|---|---|

## Example runs (step 2, 9)

| example | uv | pip | poetry |
|---|---|---|---|

## Browser checks (step 9)

## Findings
