#!/bin/sh
# F6: both ports against the same databases, in turn. Needs the three containers of F (see
# F-migrations.md; PARITY_F_*_PORT for other ports) and a Java worktree with
# ParityFCrossProbeTest in ekbatan-flyway.
# usage: cross.sh <runj.sh> <scratch dir>
RUNJ="$1"
OUT="$2"
cd "$(dirname "$0")/../.." || exit 1
py() { uv run --no-sync python parity-probes/F-migrations/f6_cross.py "$1" >> "$OUT/cross.python.log" 2>&1; }
: > "$OUT/cross.python.log"
: > "$OUT/cross.java.log"
jv() { sh "$RUNJ" "ParityFCrossProbeTest.$1" "$OUT/cross.j.$1.log" >/dev/null; cat "$OUT/cross.j.$1.log" >> "$OUT/cross.java.log"; }

# A: Java first - Java V1-V2, Python V3, Java V4, then both validate and migrate again
jv a1_java_migrates_v1_v2
py a2
jv a3_java_migrates_v4
py a4
jv a5_java_validates_and_migrates_again

# B: Python first - Python V1-V2, Java V3, Python V4, then both validate and migrate again
py b1
jv b2_java_migrates_v3
py b3
jv b4_java_validates_and_migrates_again
py b5

# C: both at once on a fresh database: each port waits for the same wall-clock instant
py c0
echo $(( $(date +%s) * 1000 + 45000 )) > "$OUT/parityF-start"
py c1 &
jv c1_java_concurrent
wait
py c2

# E: Python builds an index concurrently while Java waits on the lock (PostgreSQL)
py e0
echo $(( $(date +%s) * 1000 + 40000 )) > "$OUT/parityF-start"
py e1 &
jv e1_java_waits_while_python_builds_a_concurrent_index
wait
py e2

# E again, Java with Flyway's session lock (postgresql.transactional.lock=false)
py e0
echo $(( $(date +%s) * 1000 + 40000 )) > "$OUT/parityF-start"
py e1b &
jv e1b_java_with_a_session_lock_waits_while_python_builds_a_concurrent_index
wait
py e2
echo done
