ALTER TABLE users ADD COLUMN payment_terminal jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE custody_transfers ADD COLUMN receiving_branch_id text REFERENCES branches(id);
CREATE INDEX custody_receiving_branch_pending ON custody_transfers(receiving_branch_id,status) WHERE receiving_branch_id IS NOT NULL;
