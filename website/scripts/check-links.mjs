// Every internal link in the built site must resolve to a built page.
//
// `scripts/links.py` at the repository root checks the docs against each other, which is what
// makes them right on GitHub. It cannot check this: a docs link is rewritten on the way into the
// site, so the thing that must exist is the *output*, not the source. This catches the gap -
// and did, on the two ledgers that are deliberately not published.
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

function walk(dir) {
    return readdirSync(dir).flatMap((name) => {
        const full = join(dir, name);
        return statSync(full).isDirectory() ? walk(full) : [full];
    });
}

if (!existsSync("dist")) {
    console.error("no dist/ - run `npm run build` first");
    process.exit(1);
}

const targets = new Set();
for (const file of walk("dist")) {
    if (!file.endsWith(".html")) continue;
    for (const href of readFileSync(file, "utf8").matchAll(/href="(\/[^"#?]*)"/g)) {
        const target = href[1];
        if (target.startsWith("/_astro") || /\.(css|js|woff2?|png|svg|ico)$/.test(target)) continue;
        targets.add(target);
    }
}

const broken = [...targets].sort().filter((t) => {
    const path = t.replace(/^\/|\/$/g, "");
    return !existsSync(join("dist", path, "index.html")) && !existsSync(join("dist", path));
});

console.log(`${targets.size} distinct internal targets, ${broken.length} broken`);
for (const b of broken) console.log(`  BROKEN  ${b}`);
process.exitCode = broken.length ? 1 : 0;
