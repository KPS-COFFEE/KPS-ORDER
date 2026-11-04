-- Every paired printer requires a physical test before operational use.
ALTER TABLE branch_print_devices ADD COLUMN approved_at TIMESTAMPTZ;
ALTER TABLE branch_print_devices ADD COLUMN approved_by TEXT REFERENCES users(id);
ALTER TABLE branch_print_devices ADD COLUMN acceptance_job_id TEXT;
ALTER TABLE branch_print_jobs ALTER COLUMN order_id DROP NOT NULL;
ALTER TABLE branch_print_jobs DROP CONSTRAINT branch_print_jobs_kind_check;
ALTER TABLE branch_print_jobs ADD CONSTRAINT branch_print_jobs_kind_check CHECK(kind IN ('order','preparation','test'));
ALTER TABLE branch_print_jobs ADD CONSTRAINT branch_print_jobs_test_order_check CHECK((kind='test' AND order_id IS NULL) OR (kind<>'test' AND order_id IS NOT NULL));
