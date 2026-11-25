-- Source-backed Google place bindings update existing branches only.
-- The ledger includes unresolved source evidence without publishing a guessed pin.
CREATE TABLE owner_branch_location_decisions (
 decision_id TEXT PRIMARY KEY,
 brand_id TEXT NOT NULL,
 branch_id TEXT NOT NULL,
 branch_no INT NOT NULL,
 source_id TEXT NOT NULL,
 workbook_sha256 TEXT NOT NULL,
 evidence JSONB NOT NULL,
 result TEXT,
 evaluated_at TIMESTAMPTZ
);
