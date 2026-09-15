// Deriving a page's identity from the markdown, so a docs file needs no frontmatter.
//
// The docs are written for the repository first. Requiring frontmatter in them would put
// website concerns into files whose primary reader is somebody browsing GitHub, and would
// be one more thing to forget when adding a page.

/** `concepts/readme` -> `concepts`; `database/tables/events` -> `database/tables/events`. */
export function slugOf(id: string): string {
    const parts = id.split("/");
    if (parts[parts.length - 1].toLowerCase() === "readme") parts.pop();
    return parts.join("/");
}

/** The first `# heading`. */
export function titleOf(entry: { body?: string; id: string }): string {
    const match = entry.body?.match(/^#\s+(.+)$/m);
    return match ? stripMarkdown(match[1]) : entry.id;
}

/** The first paragraph after the title, as the page's lead and meta description. */
export function leadOf(entry: { body?: string }): string | undefined {
    const body = entry.body ?? "";
    const afterTitle = body.replace(/^[\s\S]*?^#\s+.+$/m, "");
    for (const block of afterTitle.split(/\n{2,}/)) {
        const text = block.trim();
        // Skip anything that is not prose: a heading, a fence, a table, a list, a quote.
        if (!text || /^[#>|`\-*]/.test(text)) continue;
        return stripMarkdown(text.replace(/\n/g, " ")).slice(0, 280);
    }
    return undefined;
}

/** Cut at a word boundary, not mid-word, and say that it was cut. */
export function truncate(text: string | undefined, limit: number): string {
    if (!text) return "";
    if (text.length <= limit) return text;
    const cut = text.slice(0, limit);
    const lastSpace = cut.lastIndexOf(" ");
    return (lastSpace > limit * 0.6 ? cut.slice(0, lastSpace) : cut).replace(/[,;:.]$/, "") + "…";
}

function stripMarkdown(text: string): string {
    return text
        .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
        .replace(/[`*_]/g, "")
        .trim();
}
