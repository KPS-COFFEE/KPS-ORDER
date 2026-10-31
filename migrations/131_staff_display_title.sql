-- Optional presentation metadata; no role, grant or branch changes.
ALTER TABLE users ADD COLUMN IF NOT EXISTS job_title text;
ALTER TABLE users ADD CONSTRAINT users_job_title_length CHECK (job_title IS NULL OR char_length(job_title) <= 80);
