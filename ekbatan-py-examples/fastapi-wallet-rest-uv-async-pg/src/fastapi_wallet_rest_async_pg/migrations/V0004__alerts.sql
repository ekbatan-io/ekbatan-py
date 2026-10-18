-- What the deposit handler writes: one alert per deposit, for the recipient it names.
-- Generated once with ekbatan.migrations.emit_ddl(metadata, "postgresql") and pasted, rather
-- than built from schema.py at migration time: a migration has to mean the same thing forever.
-- An applied migration is never edited; a change goes in the next file.

CREATE TABLE alerts (
    id UUID NOT NULL,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    wallet_id UUID NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (id)
);
