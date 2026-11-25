CREATE TABLE IF NOT EXISTS interview_exams (
 room_id bigint PRIMARY KEY REFERENCES interview_rooms(id),
 token_hash text UNIQUE NOT NULL, started_at timestamptz, deadline timestamptz,
 submitted_at timestamptz, paper jsonb NOT NULL, answers jsonb NOT NULL DEFAULT '{}',
 revision integer NOT NULL DEFAULT 0);
