// Every ```python block on the site must be syntactically Python.
//
// Not that it runs - most are fragments, with no imports and no database. Only that a reader
// who copies one does not get a SyntaxError from the paste. Three did: `...` after a keyword
// argument, a leading-dot method chain with no enclosing parentheses, and two columns of
// method names laid out inside a code fence.
//
// A fragment that starts mid-class or mid-function is fine: it is retried wrapped in one.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

function walk(dir) {
    return readdirSync(dir).flatMap((name) => {
        const full = join(dir, name);
        return statSync(full).isDirectory() ? walk(full) : [full];
    });
}

const blocks = [];
for (const file of walk("src/pages")) {
    if (!file.endsWith(".mdx")) continue;
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/```python\n([\s\S]*?)```/g)) {
        blocks.push({ file, line: text.slice(0, m.index).split("\n").length + 1, src: m[1] });
    }
}

const checker = `
import ast, json, sys, textwrap
bad = []
for b in json.load(sys.stdin):
    src = b["src"]
    for attempt in (src, "class _X:\\n" + textwrap.indent(src, "    "),
                    "def _f():\\n" + textwrap.indent(src, "    ")):
        try:
            ast.parse(attempt)
            break
        except SyntaxError as e:
            err = e
    else:
        bad.append({**b, "err": str(err).split("(")[0].strip(),
                    "first": (src.strip().splitlines() or [""])[0][:64]})
json.dump(bad, sys.stdout)
`;

const run = spawnSync("python3", ["-c", checker], { input: JSON.stringify(blocks), encoding: "utf8" });
if (run.status !== 0) {
    console.error(run.stderr);
    process.exit(1);
}
const bad = JSON.parse(run.stdout);
console.log(`${blocks.length} python snippets, ${bad.length} that do not parse`);
for (const b of bad) console.log(`  ${b.file}:${b.line}  ${b.err}  |  ${b.first}`);
process.exitCode = bad.length ? 1 : 0;
