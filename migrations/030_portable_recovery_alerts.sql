-- Local backup/verification/restore failures use the existing recovery alert
-- queue. They remain distinct from provider evidence and never update the
-- provider recovery_state verification source or successful-backup timestamp.
ALTER TABLE recovery_events DROP CONSTRAINT IF EXISTS recovery_events_source_check;
ALTER TABLE recovery_events ADD CONSTRAINT recovery_events_source_check
  CHECK(source IN ('signed_webhook','provider_api','provider_dashboard','runtime_monitor','restore_drill','portable_backup'));
