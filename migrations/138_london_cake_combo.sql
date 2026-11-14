-- One-time owner-approved campaign; application installs it after safe brand seeding.
CREATE TABLE IF NOT EXISTS owner_campaign_decisions (
 decision_id TEXT PRIMARY KEY,
 brand_id TEXT NOT NULL REFERENCES brands(id),
 applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
