-- Preserve owner-classified test history without inventing financial settlement.
ALTER TABLE retained_carryovers ADD COLUMN archived_test_at TIMESTAMPTZ;
ALTER TABLE retained_carryovers ADD COLUMN archived_test_by TEXT REFERENCES users(id);
ALTER TABLE retained_carryovers ADD COLUMN archived_test_reason TEXT;
ALTER TABLE retained_carryovers ADD CONSTRAINT retained_test_archive_complete CHECK (
 (archived_test_at IS NULL AND archived_test_by IS NULL AND archived_test_reason IS NULL)
 OR (archived_test_at IS NOT NULL AND archived_test_by IS NOT NULL
     AND archived_test_reason IS NOT NULL AND length(trim(archived_test_reason)) > 0
     AND to_shift_id IS NULL AND carried_at IS NULL)
);
