# Agent skills

The source of truth for this repository's portable agent skills. The same folders are mirrored
into `.claude/skills/` for Claude Code and `.agents/skills/` for Codex.

After editing a skill here, run:

```bash
agent-skills/scripts/sync.sh
```

Named `ekbatan-py-*` rather than `ekbatan-*` so that an agent with both repositories open cannot
load the Java skills against this tree. The two frameworks have different rules - Java's
maintainer skill says "use jOOQ explicitly" and "prefer `txDbElseDb`", which are the right
instructions there and near-misses here.

| skill | when |
| --- | --- |
| `ekbatan-py-maintainer` | changing the framework itself |
| `ekbatan-py-examples` | changing anything under `ekbatan-py-examples/` |
| `ekbatan-py-verification` | deciding and running what proves a change |
