-- Additive runtime observations and owner delivery references. Provider backup
-- timestamps stay distinct: a healthy web/database check is never a backup.
ALTER TABLE recovery_events DROP CONSTRAINT IF EXISTS recovery_events_source_check;
ALTER TABLE recovery_events ADD CONSTRAINT recovery_events_source_check
  CHECK(source IN ('signed_webhook','provider_api','provider_dashboard','runtime_monitor','restore_drill'));
ALTER TABLE recovery_state DROP CONSTRAINT IF EXISTS recovery_state_verification_source_check;
ALTER TABLE recovery_state ADD CONSTRAINT recovery_state_verification_source_check
  CHECK(verification_source IN ('signed_webhook','provider_api','provider_dashboard'));

CREATE TABLE IF NOT EXISTS recovery_monitor_state (
  brand_id TEXT NOT NULL REFERENCES brands(id), database_id TEXT NOT NULL,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(), metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  failing_checks JSONB NOT NULL DEFAULT '[]'::jsonb,
  PRIMARY KEY(brand_id,database_id)
);
CREATE TABLE IF NOT EXISTS recovery_alert_notifications (
  id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES recovery_alerts(id),
  recipient_user_id TEXT NOT NULL REFERENCES users(id), brand_id TEXT NOT NULL REFERENCES brands(id),
  email_outbox_id TEXT REFERENCES email_outbox(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(alert_id,recipient_user_id)
);
CREATE INDEX IF NOT EXISTS recovery_alert_notifications_waiting
  ON recovery_alert_notifications(created_at) WHERE email_outbox_id IS NULL;
