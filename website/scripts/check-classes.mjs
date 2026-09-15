// Which utility classes do our own pages use that the frozen bundle does not define?
//
// The bundle is Tailwind output tree-shaken from the Java site's HTML, so a class that site
// never used is simply absent - and using it does nothing, silently. That is exactly how a
// hand-written `md:hidden` left the MENU button visible at every width.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync("src/styles/styles.css", "utf8");

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
            if (local.has(cls)) continue;
            if (css.includes(asSelector(cls) + "{") || css.includes(asSelector(cls) + ",") ||
                css.includes(asSelector(cls) + ":") || css.includes(asSelector(cls) + " ")) continue;
            if (!missing.has(cls)) missing.set(cls, new Set());
            missing.get(cls).add(file);
        }
    }
}

if (missing.size === 0) {
    console.log("every class used is defined in the bundle or locally");
} else {
    console.log(`${missing.size} class(es) used but not defined:\n`);
    for (const [cls, files] of [...missing].sort()) {
        console.log(`  ${cls.padEnd(34)} ${[...files].join(", ")}`);
    }
    process.exitCode = 1;
}
