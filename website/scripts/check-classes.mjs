// Which utility classes do our own pages use that the frozen bundle does not define?
//
// The bundle is Tailwind output tree-shaken from the Java site's HTML, so a class that site
// never used is simply absent - and using it does nothing, silently. That is exactly how a
// hand-written `md:hidden` left the MENU button visible at every width.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync("src/styles/styles.css", "utf8");

// Classes the *Java* site also uses without the bundle defining them - verified by running this
// script against ../ekbatan/website. The matcher below is deliberately naive about minified
// selectors, so these are mostly its own false positives; the few that are real are Java's
// too, and render identically there. What must stay empty is anything outside this set.
const KNOWN = new Set(["...", "decoration-2", "example-row", "group", "hover:-translate-x-1", "hover:text-yellow", "lg:grid-cols-[1fr_2fr]", "mb-12", "md:p-14", "md:p-8", "md:py-20", "md:text-3xl", "md:text-7xl", "md:text-lg", "min-w-[160px]", "mt-3", "pb-2", "pb-8", "previous", "row-old-one", "row-old-two", "shiki", "space-y-14", "space-y-2", "space-y-4", "space-y-8", "text-5xl", "text-[12px]", "text-ink/60", "underline", "w-20", "whitespace-pre"]);

function walk(dir) {
    return readdirSync(dir).flatMap((name) => {
        const full = join(dir, name);
        return statSync(full).isDirectory() ? walk(full) : [full];
    });
}

// Escape a class name the way Tailwind does when emitting a selector.
const asSelector = (cls) => "." + cls.replace(/([:\/\[\]\.%#\(\),])/g, "\\$1");

const local = new Set();
for (const file of walk("src")) {
    if (!/\.(astro|mdx)$/.test(file)) continue;
    const text = readFileSync(file, "utf8");
    // Classes defined in the file's own <style> block are fine.
    for (const m of text.matchAll(/\.([a-zA-Z][\w-]*)\s*[,{]/g)) local.add(m[1]);
}

const missing = new Map();
for (const file of walk("src")) {
    if (!/\.(astro|mdx)$/.test(file)) continue;
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/class(?::list)?=["']([^"']+)["']/g)) {
        for (const cls of m[1].split(/\s+/)) {
            if (!cls || cls.includes("{") || cls.includes("$")) continue;
            if (local.has(cls) || KNOWN.has(cls)) continue;
            if (css.includes(asSelector(cls) + "{") || css.includes(asSelector(cls) + ",") ||
                css.includes(asSelector(cls) + ":") || css.includes(asSelector(cls) + " ")) continue;
            if (!missing.has(cls)) missing.set(cls, new Set());
            missing.get(cls).add(file);
        }
    }
}

if (missing.size === 0) {
    console.log(`every class resolves (${KNOWN.size} known-shared with the Java site)`);
} else {
    console.log(`${missing.size} class(es) used but not defined:\n`);
    for (const [cls, files] of [...missing].sort()) {
        console.log(`  ${cls.padEnd(34)} ${[...files].join(", ")}`);
    }
    process.exitCode = 1;
}
