-- One-time owner-approved campaign; application installs it after safe brand seeding.
CREATE TABLE IF NOT EXISTS owner_campaign_decisions (
 decision_id TEXT PRIMARY KEY,
 applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
