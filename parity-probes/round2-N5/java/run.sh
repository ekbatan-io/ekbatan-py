#!/bin/sh
# run.sh <pg|mysql|mariadb> <method>  - runs one probe method, prints its PROBE lines
J=/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/r2java-N5
export JAVA_HOME=$HOME/.sdkman/candidates/java/25.0.3-graal
cd $J && ./gradlew :ekbatan-integration-tests-local-event-handler-$1:test --tests "io.ekbatan.test.local_event_handler_$1.ParityN5ProbeTest.$2" -i --rerun-tasks -x spotlessCheck \
  -Dorg.gradle.java.installations.paths=$JAVA_HOME -Dorg.gradle.java.installations.auto-detect=false > /tmp/n5-$1-$2.log 2>&1
grep -h "PROBE " /tmp/n5-$1-$2.log | sed 's/^ *//'
grep -h "FAILED\|AssertionError\|Exception:" /tmp/n5-$1-$2.log | grep -v "PROBE" | head -5
