-- KPS owner Friday hours apply after the immutable historical branch import.
-- All existing owner overrides, explicit pauses and operating data survive.
-- The verified 1–20 branch identities are evaluated exactly once.
CREATE TABLE branch_opening_hours_decisions (
 branch_id TEXT PRIMARY KEY REFERENCES branches(id),
 decision_id TEXT NOT NULL,
 result TEXT NOT NULL,
 evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
