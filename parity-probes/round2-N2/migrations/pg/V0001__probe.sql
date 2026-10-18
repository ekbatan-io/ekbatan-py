-- Area N2 probe schema (PostgreSQL). The same file is fed to jOOQ codegen (Java) and schemagen (Python).
CREATE TABLE probe_items (
    id            UUID           PRIMARY KEY,
    version       BIGINT         NOT NULL,
    state         VARCHAR(24)    NOT NULL,
    owner_id      UUID           NOT NULL,
    ext_id        VARCHAR(36),
    currency      CHAR(3)        NOT NULL,
    balance       NUMERIC(19, 4) NOT NULL,
    bare_numeric  NUMERIC,
    ratio         DOUBLE PRECISION,
    real_val      REAL,
    qty           INTEGER        NOT NULL DEFAULT 0,
    small_val     SMALLINT,
    flag          BOOLEAN        NOT NULL DEFAULT FALSE,
    note          TEXT,
    payload       JSONB,
    plain_json    JSON,
    created_date  TIMESTAMP      NOT NULL,
    precise_ts    TIMESTAMP(3),
    zoned_ts      TIMESTAMPTZ,
    birth_day     DATE,
    clock_time    TIME,
    raw_bytes     BYTEA,
    tags          TEXT[],
    status        VARCHAR(10)    NOT NULL DEFAULT 'NEW',
    parent_id     UUID REFERENCES probe_items(id),
    "camelCol"    VARCHAR(5),
    "class"       INTEGER,
    UNIQUE (owner_id, currency)
);
CREATE INDEX idx_probe_items_state ON probe_items(state);
CREATE UNIQUE INDEX uq_probe_items_ext ON probe_items(ext_id);
CREATE VIEW probe_view AS SELECT id, balance FROM probe_items;
CREATE TABLE "order" (id UUID PRIMARY KEY, total NUMERIC(10, 2));
CREATE SCHEMA IF NOT EXISTS audit;
CREATE TABLE audit.entries (id UUID PRIMARY KEY, note TEXT NOT NULL);
