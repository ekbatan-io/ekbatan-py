#!/bin/sh
# Builds the SQL folders both ports' probes migrate. Rebuilds sql/ from scratch.
set -e
cd "$(dirname "$0")"
rm -rf sql && mkdir sql && cd sql
v1='CREATE TABLE t_v1 (id INT);'

# --- naming: what each port accepts, and in which order it applies it
mkdir -p accept/sub
echo "$v1" > accept/V1__first.sql
echo 'CREATE TABLE t_v1_5 (id INT);' > accept/V1_5__underscore_version.sql
echo 'CREATE TABLE t_v1_9 (id INT);' > accept/V1.9__nine.sql
echo 'CREATE TABLE t_v1_10 (id INT);' > accept/V1.10__ten.sql
echo 'CREATE TABLE t_v2 (id INT);' > accept/V2__two.sql
echo 'CREATE TABLE t_v3 (id INT);' > accept/sub/V3__in_a_subfolder.sql
echo 'CREATE TABLE t_v4 (id INT);' > accept/V4__capital_ext.SQL
echo 'CREATE TABLE t_v5 (id INT);' > accept/V5__.sql
echo 'CREATE TABLE t_v10 (id INT);' > accept/V10__ten_major.sql
echo 'CREATE TABLE t_v20 (id INT);' > accept/V20__gap_after_ten.sql
echo 'CREATE OR REPLACE VIEW v_b AS SELECT 1 AS x;' > accept/R__b_view.sql
echo 'CREATE OR REPLACE VIEW v_a AS SELECT 1 AS x;' > accept/R__a_view.sql
echo 'CREATE OR REPLACE VIEW v_z AS SELECT 1 AS x;' > accept/R__Z_capital.sql

# --- one odd file beside a good V1 each
odd() { mkdir -p "$1"; echo "$v1" > "$1/V1__first.sql"; }
odd dup_v1_v1.0;      echo 'CREATE TABLE t_dup (id INT);' > dup_v1_v1.0/V1.0__again.sql
odd dup_v1_v01;       echo 'CREATE TABLE t_dup (id INT);' > dup_v1_v01/V01__again.sql
odd dup_repeatable;   echo 'CREATE OR REPLACE VIEW v_ab AS SELECT 1 AS x;' > dup_repeatable/R__a_b.sql
                      echo 'CREATE OR REPLACE VIEW v_ab AS SELECT 2 AS x;' > "dup_repeatable/R__a b.sql"
odd lowercase_v;      echo 'CREATE TABLE t_lower (id INT);' > lowercase_v/v2__lower.sql
odd misnamed;         echo 'CREATE TABLE t_mis (id INT);' > misnamed/create_stuff.sql
odd repeatable_with_version; echo 'CREATE TABLE t_r1 (id INT);' > repeatable_with_version/R1__x.sql
odd undo;             echo 'DROP TABLE t_v1;' > undo/U1__first.sql
odd conf;             echo 'executeInTransaction=false' > conf/V1__first.sql.conf
odd callback;         echo 'CREATE TABLE t_callback (id INT);' > callback/afterMigrate.sql
odd post_apply;       echo 'CREATE TABLE t_post_apply (id INT);' > post_apply/post-apply.sql
odd python_file;      echo 'print(1)' > python_file/V2__code.py
odd non_utf8;         printf 'CREATE TABLE t_latin (id INT); -- caf\351\n' > non_utf8/V2__latin1.sql
long=$(printf 'd%.0s' $(seq 1 210))
odd long_description; echo 'CREATE TABLE t_long (id INT);' > "long_description/V2__$long.sql"
mkdir -p placeholder; printf "CREATE TABLE t_ph (v VARCHAR(80));\nINSERT INTO t_ph VALUES ('\${unknown_key}');\n" > placeholder/V1__first.sql
mkdir -p placeholder_builtin; printf "CREATE TABLE t_ph (v VARCHAR(80));\nINSERT INTO t_ph VALUES ('\${flyway:table}');\n" > placeholder_builtin/V1__first.sql
mkdir -p delimiter_mysql; printf 'DELIMITER //\nCREATE PROCEDURE p1() BEGIN SELECT 1; END //\nDELIMITER ;\n' > delimiter_mysql/V1__proc.sql
mkdir -p hidden/.hidden; echo "$v1" > hidden/V1__first.sql; echo 'CREATE TABLE t_hidden (id INT);' > hidden/.hidden/V2__hidden.sql
mkdir -p empty; touch empty/README.txt

# --- lifecycle: one base and its variants
base() { mkdir -p "life/$1"
  printf "CREATE TABLE life_a (id INT PRIMARY KEY, name VARCHAR(20));\nINSERT INTO life_a VALUES (1, 'x');\n" > "life/$1/V1__create.sql"
  echo 'CREATE TABLE life_b (id INT);' > "life/$1/V2__more.sql"
  echo 'CREATE OR REPLACE VIEW life_v AS SELECT id FROM life_a;' > "life/$1/R__view.sql"; }
base base
base edited_v1;   printf "CREATE TABLE life_a (id INT PRIMARY KEY, name VARCHAR(20));\nINSERT INTO life_a VALUES (1, 'y');\n" > life/edited_v1/V1__create.sql
base crlf_v1;     printf "CREATE TABLE life_a (id INT PRIMARY KEY, name VARCHAR(20));\r\nINSERT INTO life_a VALUES (1, 'x');\r\n" > life/crlf_v1/V1__create.sql
base joined_v1;   printf "CREATE TABLE life_a (id INT PRIMARY KEY, name VARCHAR(20));INSERT INTO life_a VALUES (1, 'x');\n" > life/joined_v1/V1__create.sql
base renamed_v1;  mv life/renamed_v1/V1__create.sql life/renamed_v1/V1__create_renamed.sql
base respelled_v1; mv life/respelled_v1/V1__create.sql life/respelled_v1/V001__create.sql
base out_of_order; echo 'CREATE TABLE life_late (id INT);' > life/out_of_order/V1_5__late.sql
base missing_v2;  rm life/missing_v2/V2__more.sql
base repeatable_changed; echo 'CREATE OR REPLACE VIEW life_v AS SELECT id, name FROM life_a;' > life/repeatable_changed/R__view.sql
base failing;     printf "CREATE TABLE life_c (id INT);\nINSERT INTO no_such_table VALUES (1);\n" > life/failing/V3__half.sql
base fixed;       printf "CREATE TABLE life_c (id INT);\n" > life/fixed/V3__half.sql
base future;      echo 'CREATE TABLE life_f (id INT);' > life/future/V9__future.sql

# --- the lock: one slow migration per dialect
mkdir -p lock_pg lock_mysql quick
printf 'SELECT pg_sleep(4);\nCREATE TABLE slow_t (id INT);\n' > lock_pg/V1__slow.sql
printf 'DO SLEEP(4);\nCREATE TABLE slow_t (id INT);\n' > lock_mysql/V1__slow.sql
echo 'CREATE TABLE quick_t (id INT);' > quick/V1__quick.sql

# --- a PostgreSQL statement that cannot run in a transaction, with no marker
mkdir -p concurrent_index_pg
echo 'CREATE TABLE ci (id INT);' > concurrent_index_pg/V1__table.sql
echo 'CREATE INDEX CONCURRENTLY ci_idx ON ci (id);' > concurrent_index_pg/V2__index.sql

# --- a migration that is a Python script: does anything run it?
mkdir -p script_migration
echo "$v1" > script_migration/V1__first.sql
echo "open('/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/parityF-script-ran', 'w').write('ran')" > script_migration/V2__touch.py

# --- F6 turns (parity fix M): one database migrated by both ports in turn. Cumulative folders:
# a = V1, V1_5, V2 and a repeatable; b adds V3; c adds V4.
for f in a b c; do
    mkdir -p "turns/$f"
    echo 'CREATE TABLE turns_v1 (id INT);' > "turns/$f/V1__first.sql"
    echo 'CREATE TABLE turns_v1_5 (id INT);' > "turns/$f/V1_5__underscore.sql"
    echo 'CREATE TABLE turns_v2 (id INT);' > "turns/$f/V2__second.sql"
    echo 'CREATE OR REPLACE VIEW turns_view AS SELECT 1 AS n;' > "turns/$f/R__turns_view.sql"
done
for f in b c; do echo 'CREATE TABLE turns_v3 (id INT);' > "turns/$f/V3__third.sql"; done
echo 'CREATE TABLE turns_v4 (id INT);' > turns/c/V4__fourth.sql

# --- F6 a concurrent index built while the other port waits on the lock
mkdir -p cic_pg
echo 'CREATE TABLE cic_t (id INT);' > cic_pg/V1__table.sql
echo 'SELECT pg_sleep(4);' > cic_pg/V2__slow.sql
echo 'CREATE INDEX CONCURRENTLY cic_idx ON cic_t (id);' > cic_pg/V3__index.sql
