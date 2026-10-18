-- Area N2 probe schema (MySQL). The same file is fed to jOOQ codegen (Java) and schemagen (Python).
CREATE TABLE probe_items (
    id            CHAR(36)       NOT NULL PRIMARY KEY,
    version       BIGINT         NOT NULL,
    state         VARCHAR(24)    NOT NULL,
    owner_id      CHAR(36)       NOT NULL,
    ext_id        VARCHAR(36),
    country_name  CHAR(36),
    currency      CHAR(3)        NOT NULL,
    balance       DECIMAL(19, 4) NOT NULL,
    ratio         DOUBLE,
    qty           INT            NOT NULL DEFAULT 0,
    small_val     SMALLINT,
    flag          BOOLEAN        NOT NULL DEFAULT FALSE,
    note          TEXT,
    payload       JSON,
    created_date  DATETIME(6)    NOT NULL,
    whole_secs    DATETIME,
    ts_col        TIMESTAMP(6)   NULL,
    birth_day     DATE,
    clock_time    TIME,
    raw_bytes     BLOB,
    bin_id        BINARY(16),
    status        ENUM('NEW', 'DONE') NOT NULL DEFAULT 'NEW',
    parent_id     CHAR(36),
    UNIQUE KEY uq_owner_currency (owner_id, currency),
    CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES probe_items(id)
);
CREATE INDEX idx_probe_items_state ON probe_items(state);
CREATE VIEW probe_view AS SELECT id, balance FROM probe_items;
