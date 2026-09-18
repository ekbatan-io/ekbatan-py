/**
 * Wraps every table in a horizontally scrollable div.
 *
 * `DocLayout` did this from a script at load, which is one frame too late: a table wider
 * than the column pushed the whole page sideways until the script ran, and forever with
 * JavaScript off. Markdown emits a bare `<table>` and has no syntax for wrapping one, so
 * the wrapping belongs in the build. The script stays as the fallback for a table an
 * .astro component renders, which never reaches this pipeline.
 *
 * A table already inside a wrapper is left alone, so hand-written HTML stays in control.
 */
export default function rehypeTableScroll() {
    return (tree) => {
        const walk = (node) => {
            if (!node.children) return;
            node.children = node.children.map((child) => {
                walk(child);
                if (child.type !== "element" || child.tagName !== "table") return child;
                const classes = node.properties?.className;
                const already = Array.isArray(classes)
                    ? classes.includes("table-scroll-wrap")
                    : classes === "table-scroll-wrap";
                if (already) return child;
                return {
                    type: "element",
                    tagName: "div",
                    properties: { className: ["table-scroll-wrap"] },
                    children: [child],
                };
            });
        };
        walk(tree);
    };
}
