"""Every relative link in the user-facing markdown points at a file that exists (anchors not checked)."""
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
files = [ROOT/"README.md", ROOT/"AGENTS.md", ROOT/"DIVERGENCE.md", *ROOT.glob("agent-skills/**/*.md"),
         *[p for p in (ROOT/"docs").rglob("*.md") if not re.search(r"behaviour-parity|parity-fix|parity-round|dracula/|driver-survey", str(p))]]
bad = 0
for f in files:
    for m in re.finditer(r"\]\(([^)#\s]+)(#[^)]*)?\)", f.read_text()):
        t = m.group(1)
        if re.match(r"[a-z]+:", t): continue
        if not (f.parent / t).exists():
            bad += 1; print(f"PROBE broken-link {f.relative_to(ROOT)}: {t}")
print("PROBE broken-links-total:", bad)
