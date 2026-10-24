ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS activity_brand_id TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS activity_tracking BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS audit_employee_activity ON audit_events(actor_id,created_at,id);
CREATE INDEX IF NOT EXISTS audit_activity_session ON audit_events(session_id,created_at);
