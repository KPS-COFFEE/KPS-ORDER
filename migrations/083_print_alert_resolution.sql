ALTER TABLE branch_print_jobs ADD COLUMN IF NOT EXISTS alert_resolution jsonb NOT NULL DEFAULT '{}'::jsonb;
