-- Prospective automatic work missions. No historical acceptance/return backfill.
ALTER TABLE driver_work_trips ADD COLUMN planned_return_branch_id text REFERENCES branches(id);
ALTER TABLE driver_work_trips ADD COLUMN auto_return_state jsonb NOT NULL DEFAULT '{}';
ALTER TABLE driver_work_trips ADD COLUMN auto_task_ids jsonb NOT NULL DEFAULT '[]';
