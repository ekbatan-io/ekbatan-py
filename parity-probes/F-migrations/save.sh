#!/bin/sh
# usage: save.sh <probe name> <gradle log> <python output>
# Keeps only the PROBE lines of each, as <name>.java.out and <name>.python.out, and compares them.
cd "$(dirname "$0")" || exit 1
grep -o 'PROBE .*' "$2" | awk '!seen[$0]++' > "$1.java.out"
grep -o 'PROBE .*' "$3" > "$1.python.out"
python3 compare.py "$1.java.out" "$1.python.out" > "$1.compare.txt"
grep -c '^=' "$1.compare.txt"
grep -c '^DIFF' "$1.compare.txt"
