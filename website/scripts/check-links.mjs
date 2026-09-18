// Every internal link in the built site must resolve to a built page.
//
// `scripts/links.py` at the repository root checks the docs against each other, which is what
// makes them right on GitHub. It cannot check this: a docs link is rewritten on the way into the
// site, so the thing that must exist is the *output*, not the source. This catches the gap -
// and did, on the two ledgers that are deliberately not published.
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

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
// Relative hrefs are checked per page, because where they resolve to depends on where they
// are. `[text](../../ekbatan-py-examples/x)` reads correctly in an editor and 404s once
// deployed - the repository is not next to the pages. Thirteen of those shipped before this
// looked for them.
const escaping = [];
const selfLinks = [];

for (const file of walk("dist")) {
    if (!file.endsWith(".html")) continue;
    const page = dirname(file);
    const pageRoute = "/" + relative("dist", page).replace(/\\/g, "/");
    const html = readFileSync(file, "utf8");
    // The nav marks the current section and the footer repeats every page; both point at
    // themselves on purpose. Only prose self-links are dead ends.
    const prose = html.replace(/<(nav|footer|header)\b[\s\S]*?<\/\1>/gi, " ");
    for (const match of html.matchAll(/href="([^"]*)"/g)) {
        const href = match[1];
        if (/^([a-z]+:|#|\/\/)/i.test(href) || href === "") continue;
        const target = href.replace(/[#?].*$/, "");
        if (target === "") continue;
        if (/\.(css|js|woff2?|png|svg|ico)$/.test(target)) continue;

        if (target.startsWith("/")) {
            if (!target.startsWith("/_astro")) targets.add(target);
            // A `See also` entry pointing at the page it sits on is a dead end wearing a link.
            if (
                !href.includes("#") &&
                target.replace(/\/$/, "") === pageRoute &&
                prose.includes(`href="${href}"`)
            ) {
                selfLinks.push(`${relative("dist", file)} -> ${href}`);
            }
            continue;
        }
        const resolved = resolve(page, target);
        if (!existsSync(join(resolved, "index.html")) && !existsSync(resolved)) {
            escaping.push(`${relative("dist", file)} -> ${href}`);
        }
    }
}

const broken = [...targets].sort().filter((t) => {
    const path = t.replace(/^\/|\/$/g, "");
    return !existsSync(join("dist", path, "index.html")) && !existsSync(join("dist", path));
});

console.log(
    `${targets.size} distinct internal targets, ${broken.length} broken` +
        (escaping.length ? `, ${escaping.length} relative links resolving outside the site` : "") +
        (selfLinks.length ? `, ${selfLinks.length} self-links` : ""),
);
for (const b of broken) console.log(`  BROKEN  ${b}`);
for (const e of [...new Set(escaping)].sort()) console.log(`  ESCAPES  ${e}`);
for (const s of [...new Set(selfLinks)].sort()) console.log(`  SELF     ${s}`);
process.exitCode = broken.length + escaping.length + selfLinks.length ? 1 : 0;
