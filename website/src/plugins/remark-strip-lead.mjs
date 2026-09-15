/**
 * Removes the first `# heading` and the paragraph under it from a `docs/*.md` file, because
 * the layout has already rendered both - the heading as the page title, the paragraph as the
 * lead under it. Without this every reference page says its own name twice.
 *
 * Scoped to docs/ by path. The `/learn/` pages are MDX with real frontmatter and a body that
 * starts at `##`, so they must be left alone.
 */
export default function remarkStripLead() {
    return (tree, file) => {
        const from = file.history?.[0] ?? "";
        if (!from.includes("/docs/")) return;

        const children = tree.children ?? [];
        const at = children.findIndex((n) => n.type === "heading" && n.depth === 1);
        if (at === -1) return;

        // The heading, plus the paragraph immediately after it if there is one. Anything
        // else - a table, a list, a fence - is content and stays.
        const next = children[at + 1];
        children.splice(at, next && next.type === "paragraph" ? 2 : 1);
    };
}
