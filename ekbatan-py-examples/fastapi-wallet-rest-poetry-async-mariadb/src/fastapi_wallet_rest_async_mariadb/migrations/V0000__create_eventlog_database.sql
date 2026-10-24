-- MariaDB has no schemas inside a database: a schema *is* a database. So `eventlog`, where the
-- framework keeps its two event tables, is a database of its own beside this one - one per
-- server, shared by every database on it. Created from this database's connection, so its login
-- needs the right to create a database and to use it: root here, in compose.yaml and the tests.
CREATE DATABASE IF NOT EXISTS eventlog;
