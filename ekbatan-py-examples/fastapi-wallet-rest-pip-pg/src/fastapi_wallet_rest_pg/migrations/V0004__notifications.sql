-- What the deposit handler writes: one notification per deposit, for the recipient it names.
-- Notifications are not sharded: every one is written to the default shard, whichever shard its
-- wallet is on, so on the other shards this table stays empty.
-- An applied migration is never edited; a change goes in the next file.

CREATE TABLE notifications (
    id UUID NOT NULL,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    wallet_id UUID NOT NULL,
    kind VARCHAR(48) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX idx_notifications_wallet_id ON notifications (wallet_id);
