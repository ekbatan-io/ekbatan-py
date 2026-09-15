// Count PARITY.md the way scripts/ledger.py counts the source ledgers.
import { readFileSync } from "node:fs";

const rows = readFileSync(new URL("../PARITY.md", import.meta.url), "utf8")
    .split("\n")
    .filter((l) => l.startsWith("|") && !/^\|\s*(Java|---)/.test(l))
    .map((l) => l.split("|").map((c) => c.trim()))
    .map((cells) => cells[cells.length - 2] ?? "")
    .map((status) => status.split(/[\s-]/)[0].toLowerCase())
    .filter((s) => ["done", "partial", "todo", "n/a"].includes(s));

const tally = rows.reduce((acc, s) => ({ ...acc, [s]: (acc[s] ?? 0) + 1 }), {});
const settled = (tally.done ?? 0) + (tally["n/a"] ?? 0);
for (const s of ["done", "partial", "todo", "n/a"]) {
    console.log(`  ${s.padEnd(9)}${String(tally[s] ?? 0).padStart(4)}`);
}
console.log(`\n  settled (done + n/a): ${settled}/${rows.length} = ${Math.round((100 * settled) / rows.length)}%`);
