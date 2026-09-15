// Which of `docs/` becomes a website page.
//
// One list, imported by both `content.config.ts` (which decides what to render) and
// `remark-doc-links.mjs` (which decides where a link points). Two lists would drift, and the
// symptom is a link to a page that was never built - which is exactly what happened before this
// existed.
//
// The excluded four are repository bookkeeping: they are for somebody working *on* the port,
// not somebody using it, and a reader who follows one wants the file on GitHub.
export const UNPUBLISHED = [
    "JAVA-PARITY-LEDGER",
    "BEYOND-CODE-LEDGER",
    "DECISIONS-TO-REVIEW",
];

export const GLOB_EXCLUDES = UNPUBLISHED.map((name) => `!${name}.md`);

/** Does this docs-relative path (no extension) get a page? */
export function isPublished(slug) {
    return !UNPUBLISHED.includes(slug.split("/").pop());
}
