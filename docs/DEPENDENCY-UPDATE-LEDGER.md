# Ledger: dependencies, Actions and the weekly check, as the Java repository's

Asked on 2026-11-02: do for this repository what was done for the Java one - update the
dependencies, the GitHub Actions and the website's npm packages, add Dependabot and the weekly
dependency check - and first say whether anything has a known vulnerability. The rule throughout
(the user's, since 2026-11-01): a release is taken only once it is 14 days old. Judgement calls
are in [DECISIONS-TO-REVIEW.md](DECISIONS-TO-REVIEW.md#dependencies-kept-current-as-javas-are-ekb-0592-).

## Before: known vulnerabilities (2026-11-02)

Every package pinned in the two committed lock files, looked up on osv.dev; the website's
`npm audit`; the Actions the workflows use. The examples commit no lock file.

| Where | Package | Severity | Fixed in |
|---|---|---|---|
| `uv.lock`, `db-scheduler/uv.lock` (the `codegen` extra and the test tools, through testcontainers) | certifi 2026.7.22 | critical | 2026.10.18 |
| the same | urllib3 2.7.0 | 2 high, 1 moderate | 2.8.0 |
| `uv.lock` (a core dependency, as pinned) | opentelemetry-api 1.44.0 | moderate | 1.45.1 |
| `website/package-lock.json` (build tools) | astro, devalue, sharp, smol-toml, source-map-js, undici, vite, zod | 6 high, 1 moderate, 1 low | Astro 7.4 and a fresh lock |
| `.github/workflows/` | the Actions | none known; older majors | - |

GitHub showed no alerts because its dependency graph read no file of this repository at all.

## Steps

| # | Step | State |
|---|---|---|
| 1 | Scan the lock files, the website and the Actions | done - the table above |
| 2 | `exclude-newer = "14 days"` in both `pyproject.toml` files; `uv lock --upgrade` in the root and in `db-scheduler/` | done - 0 advisories in either lock, nothing in them under 14 days old |
| 3 | The website's npm packages: Astro 7.4.0, MDX 8.1.0, markdown-remark 7.3.3, a fresh lock from releases 14 days old; the plugins into `unified()` | done - `npm audit` 0; all 59 pages the same as before, HTML, CSS and scripts; `npm run check` passes |
| 4 | The Actions to their current majors: checkout v7, setup-uv v10, setup-node v7, upload-artifact v7, download-artifact v8, upload-pages-artifact v5, deploy-pages v5; the website on Node 26 | done - the breaking changes read against how each is used |
| 5 | `.github/dependabot.yml`: the Actions and the website's npm packages, weekly, a 14-day cooldown | done |
| 6 | The weekly dependency check: `.github/workflows/dependency-check.yml`, `report.py`, `issue.py`, `held-back.txt`, and the lock files sent to GitHub | done - run on the old locks (finds the 9 advisories, sorts them) and the new ones (finds none), and as of a later day (lists the releases then old enough) |
| 7 | Verify locally: lint; unit and SQLite; every dialect; psycopg async; gevent; tracing; the scheduler port | done - lint, unit (2253) and SQLite (3393), gevent (2997), tracing (3392) passed at once; every dialect found 5 failures, all SQLAlchemy 2.1 (step 7a), and one MySQL container that did not start in 120 s, which passed when rerun; the scheduler port's ruff, mypy and unit tests pass, and its integration suite does but for one timing test that fails now and then on SQLAlchemy 2.0.52 too. The examples were not run: nothing in them changed, and the nightly runs every one |
| 7a | SQLAlchemy 2.1 (out since 2026-09-24, so what a fresh install already gets): a PostgreSQL `BIT` reads as its `BitString`; and the schema generator wrote a domain with a collation as `COLLATE "DOMAIN <name>" COLLATE ...` - our call of SQLAlchemy's private `_reflect_type` by position, whose parameters 2.1 changed | done - the call fixed for 2.0 and 2.1, the type tests expect `BitString`, the docs sentence on `BIT` updated in both trees; the 5 failures pass, on psycopg sync and async, and so does the rest of the generator's suite on PostgreSQL |
| 8 | Commit on main; push when the user asks | done - EKB-0592 to EKB-0596, on main; not pushed |
| 9 | After the push: CI, the website deploy, the first dependency check, the nightly | todo |
| 10 | "Dependency graph" on in the repository settings, so GitHub takes the submitted lock files | todo - the user's to switch on |
