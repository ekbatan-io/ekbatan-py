#!/usr/bin/env bash
# Runs QuartzOracle: Java db-scheduler's own cron engine (cron-utils 9.2.1, CronType.QUARTZ).
# Reads "pattern TAB zone TAB start TAB count" lines on stdin; see QuartzOracle.java.
# Needs a JDK (17+) and network access the first time, to fetch two jars from Maven Central.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cache="${QUARTZ_ORACLE_CACHE:-$here/.cache}"
mkdir -p "$cache/lib" "$cache/classes"
fetch() { # path sha1
  local jar="$cache/lib/$(basename "$1")"
  if [ ! -f "$jar" ]; then
    curl -sSfL -o "$jar.part" "https://repo1.maven.org/maven2/$1"
    mv "$jar.part" "$jar"
  fi
  echo "$2  $jar" | shasum -a 1 -c --quiet - || { rm -f "$jar"; echo "checksum mismatch: $1" >&2; exit 1; }
}
fetch com/cronutils/cron-utils/9.2.1/cron-utils-9.2.1.jar 5d3738bc7a2eaa45a94a76c6e87af54a95414637
fetch org/slf4j/slf4j-api/2.0.17/slf4j-api-2.0.17.jar d9e58ac9c7779ba3bf8142aff6c830617a7fe60f
if [ ! -f "$cache/classes/QuartzOracle.class" ] || [ "$here/QuartzOracle.java" -nt "$cache/classes/QuartzOracle.class" ]; then
  javac -cp "$cache/lib/*" -d "$cache/classes" "$here/QuartzOracle.java"
fi
exec java -XX:-OmitStackTraceInFastThrow -cp "$cache/classes:$cache/lib/*" QuartzOracle
