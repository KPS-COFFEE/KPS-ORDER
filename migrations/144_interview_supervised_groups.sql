ALTER TABLE interview_exams ADD COLUMN IF NOT EXISTS group_opens_at timestamptz;
ALTER TABLE interview_rooms ADD COLUMN IF NOT EXISTS track text NOT NULL DEFAULT 'finance';
