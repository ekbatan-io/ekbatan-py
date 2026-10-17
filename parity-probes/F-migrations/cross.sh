#!/bin/sh
# F6: both ports against the same databases, in turn. Needs the three containers of F (see
# F-migrations.md) and a Java worktree with ParityFCrossProbeTest in ekbatan-flyway.
# usage: cross.sh <runj.sh> <scratch dir>
RUNJ="$1"
OUT="$2"
cd "$(dirname "$0")/../.." || exit 1
py() { uv run --no-sync python parity-probes/F-migrations/f6_cross.py "$1" >> "$OUT/cross.python.log" 2>&1; }
: > "$OUT/cross.python.log"
: > "$OUT/cross.java.log"
jv() { sh "$RUNJ" "ParityFCrossProbeTest.$1" "$OUT/cross.j.$1.log" >/dev/null; cat "$OUT/cross.j.$1.log" >> "$OUT/cross.java.log"; }

jv a1_java_first
py a2
py a3
jv a4_java_continues_after_python_baselined
py a5
py b1
jv b2_java_after_python

# both at once on a fresh database: each port waits for the same wall-clock instant
py c0
echo $(( $(date +%s) * 1000 + 45000 )) > "$OUT/parityF-start"
py c1 &
jv c1_java_concurrent
wait
py c2
echo done
