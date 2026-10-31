-- Existing collection identities and amounts remain immutable. A legacy row
-- retains its original unique method slot; new pieces have their own identity.
ALTER TABLE collections ADD COLUMN IF NOT EXISTS payment_piece text NOT NULL DEFAULT 'legacy';
CREATE UNIQUE INDEX IF NOT EXISTS collections_order_revision_method_piece_unique
    ON collections(order_id,amendment_revision,method,payment_piece);
DROP INDEX IF EXISTS collections_order_revision_method_unique;
