#!/usr/bin/env bash
# Mirror the skills into the directories the agents actually discover.
#
# Two targets rather than Java's three: there is no plugin distribution for this repository.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
skills=(
  ekbatan-py-maintainer
  ekbatan-py-examples
  ekbatan-py-verification
)

for target in "$root/.claude/skills" "$root/.agents/skills"; do
  mkdir -p "$target"
  for skill in "${skills[@]}"; do
    rm -rf "$target/$skill"
    cp -R "$root/agent-skills/$skill" "$target/$skill"
  done
done

echo "Synced skills to .claude and .agents."
