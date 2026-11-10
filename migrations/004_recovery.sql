CREATE TABLE IF NOT EXISTS recovery_events (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL REFERENCES brands(id),
  database_id TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('started','succeeded','failed','observed')),
  source TEXT NOT NULL CHECK(source IN ('signed_webhook','provider_api')),
  request_digest TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(database_id,provider_event_id)
);
CREATE INDEX IF NOT EXISTS recovery_events_scope ON recovery_events(brand_id,database_id,occurred_at DESC);
CREATE TABLE IF NOT EXISTS recovery_state (
  brand_id TEXT NOT NULL REFERENCES brands(id), database_id TEXT NOT NULL,
  last_success_at TIMESTAMPTZ, last_failure_at TIMESTAMPTZ,
  last_verified_at TIMESTAMPTZ NOT NULL,
  verification_source TEXT NOT NULL CHECK(verification_source IN ('signed_webhook','provider_api')),
  retention_days INTEGER CHECK(retention_days BETWEEN 1 AND 30),
  recoverable_from TIMESTAMPTZ, recoverable_until TIMESTAMPTZ,
  potential_loss_window_seconds INTEGER CHECK(potential_loss_window_seconds>=0),
  PRIMARY KEY(brand_id,database_id)
);
CREATE TABLE IF NOT EXISTS recovery_alerts (
  id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
  database_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES recovery_events(id),
  code TEXT NOT NULL, event_type TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  acknowledged_at TIMESTAMPTZ, acknowledged_by TEXT REFERENCES users(id),
  UNIQUE(event_id,code)
);
CREATE INDEX IF NOT EXISTS recovery_alerts_open ON recovery_alerts(brand_id,database_id,created_at DESC) WHERE acknowledged_at IS NULL;
