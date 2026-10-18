#!/bin/sh
# Writes the three Java probes into the throwaway Java worktree from the template.
J=/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/r2java-N5/ekbatan-integration-tests/local-event-handler
H=$(dirname "$0")
gen() { # dir pkg d dialect jdbc port locks
  out=$J/$1/src/test/java/$(echo $2 | tr . /)/ParityN5ProbeTest.java
  python3 - "$H" "$out" "$2" "$3" "$4" "$5" "$6" "$7" <<'PY'
import sys
h,out,pkg,d,dia,jdbc,port,locks=sys.argv[1:]
t=open(h+"/ParityN5ProbeTest.java.tmpl").read()
more=open(h+"/more.java.part").read()
t=t.replace("    // __MORE__\n",more)
for a,b in (("__PKG__",pkg),("__D__",d),("__DIALECT__",dia),("__JDBC__",jdbc),("__PORT__",port),("__LOCKS__",locks)):
    t=t.replace(a,b)
open(out,"w").write(t)
PY
}
gen pg io.ekbatan.test.local_event_handler_pg pg POSTGRES jdbc:postgresql 58601 "io.ekbatan.core.concurrent.PostgresKeyedLockProvider.Builder.postgresKeyedLockProvider().connectionProvider(cp).build()"
gen mysql io.ekbatan.test.local_event_handler_mysql mysql MYSQL jdbc:mysql 58602 "io.ekbatan.core.concurrent.MySQLKeyedLockProvider.Builder.mySQLKeyedLockProvider().connectionProvider(cp).build()"
gen mariadb io.ekbatan.test.local_event_handler_mariadb mariadb MARIADB jdbc:mariadb 58603 "io.ekbatan.core.concurrent.MariaDBKeyedLockProvider.Builder.mariaDBKeyedLockProvider().connectionProvider(cp).build()"
