import { visit } from "unist-util-visit";
import path from "node:path";
import { isPublished } from "../lib/published.mjs";

/**
 * Rewrites the relative links in `docs/*.md` so the same file works both on GitHub and on
 * this site.
 *
 * The docs are written to be read in the repository: `[the outbox](outbox.md)`,
 * `[app.py](../../ekbatan-py-examples/fastapi_wallet_rest_pg/app.py)`. On GitHub those
 * resolve. Rendered as a website they would 404, and `scripts/links.py` cannot help - it
 * checks the files against each other, which is exactly what makes them right for GitHub
 * and wrong for here.
 *
 * Two rules:
 *
 *   a link that stays inside docs/   ->  the matching /reference/ URL
 *   a link that escapes docs/        ->  the file on GitHub
 *
 * The second is not a fallback for links that failed to resolve. It is the correct
 * destination: `ekbatan-py-examples/` and `DIVERGENCE.md` are source, not documentation,
 * and a reader following one wants to see the file.
 *
 * @param {{ base?: string, repo?: string, branch?: string }} options
 */
export default function remarkDocLinks({
    base = "/",
    repo = "https://github.com/ekbatan-io/ekbatan-py",
    branch = "main",
} = {}) {
    const prefix = base.replace(/\/$/, "");

    return (tree, file) => {
        // Where this document sits inside docs/, e.g. "database/tables" for
        // docs/database/tables/events.md. Relative links resolve against it.
        const absolute = file.history?.[0] ?? "";
        const docsRoot = absolute.includes("/docs/")
            ? absolute.slice(0, absolute.lastIndexOf("/docs/") + 6)
            : "";
        const here = docsRoot ? path.dirname(path.relative(docsRoot, absolute)) : ".";

        visit(tree, ["link", "definition"], (node) => {
            const url = node.url;
            if (typeof url !== "string" || !url) return;
            // Absolute, external, or a bare anchor - all already correct.
            if (/^[a-z]+:/i.test(url) || url.startsWith("//") || url.startsWith("#")) return;
            if (url.startsWith("/")) {
                node.url = prefix + url;
                return;
            }

            const [target, hash] = splitHash(url);
            const resolved = path.normalize(path.join(here, target));

            // `..` that climbs past docs/ means the target is elsewhere in the repository.
            if (resolved.startsWith("..")) {
                const fromRepoRoot = path.normalize(path.join("docs", here, target));
                const kind = /\.[a-z]+$/i.test(fromRepoRoot) ? "blob" : "tree";
                node.url = `${repo}/${kind}/${branch}/${fromRepoRoot.replace(/^(\.\.\/)+/, "")}${hash}`;
                return;
            }

            // Inside docs/, but not every docs file becomes a page. One that does not is
            // repository bookkeeping, and a reader following it wants the file on GitHub.
            const withoutExtension = resolved.replace(/\.md$/i, "");
            if (!isPublished(withoutExtension)) {
                node.url = `${repo}/blob/${branch}/docs/${withoutExtension}.md${hash}`;
                return;
            }
            node.url = `${prefix}/reference/${slugOf(resolved)}${hash}`;
        });
    };
}

function splitHash(url) {
    const at = url.indexOf("#");
    return at === -1 ? [url, ""] : [url.slice(0, at), url.slice(at)];
}

/** `database/tables/events.md` -> `database/tables/events/`; a README is its directory. */
function slugOf(relative) {
    const withoutExtension = relative.replace(/\.md$/i, "");
    const parts = withoutExtension.split("/");
    if (parts[parts.length - 1].toLowerCase() === "readme") parts.pop();
    return parts.length ? parts.join("/") + "/" : "";
}
