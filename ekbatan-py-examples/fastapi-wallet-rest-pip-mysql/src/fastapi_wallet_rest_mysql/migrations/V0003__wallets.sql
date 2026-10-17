-- The application's own table.
-- Generated once with ekbatan.migrations.emit_ddl(metadata, "mysql") and pasted, rather
-- than built from schema.py at migration time: a migration has to mean the same thing forever.
-- An applied migration is never edited; a change goes in the next file.

CREATE TABLE wallets (
    id CHAR(36) NOT NULL,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    owner_id CHAR(36) NOT NULL,
    currency CHAR(3) NOT NULL,
    balance NUMERIC(19, 4) NOT NULL,
    created_date DATETIME(6) NOT NULL,
    updated_date DATETIME(6) NOT NULL,
    PRIMARY KEY (id)
);
