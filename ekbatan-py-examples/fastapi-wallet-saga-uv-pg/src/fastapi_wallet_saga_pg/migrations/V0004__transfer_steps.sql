-- The saga's step markers: one row per step a transfer has run. A replayed event finds its
-- marker and does nothing; the unique key stops two deliveries racing past that check.
-- An applied migration is never edited; a change goes in the next file.

CREATE TABLE transfer_steps (
    id UUID NOT NULL,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    transfer_id UUID NOT NULL,
    step VARCHAR(64) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (transfer_id, step)
);

CREATE INDEX idx_transfer_steps_transfer_id ON transfer_steps (transfer_id);
