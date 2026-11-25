CREATE TABLE IF NOT EXISTS interview_panels (
 owner_id text PRIMARY KEY REFERENCES users(id), member_ids text[] NOT NULL,
 expires_at timestamptz NOT NULL, closed boolean NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS interview_rooms (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id text NOT NULL REFERENCES interview_panels(owner_id),
 candidate text NOT NULL, reviewing boolean NOT NULL DEFAULT false, turns jsonb NOT NULL DEFAULT '[]',
 version integer NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS interview_responses (
 room_id bigint REFERENCES interview_rooms(id), member_id text REFERENCES users(id),
 answers jsonb NOT NULL DEFAULT '{}', submitted boolean NOT NULL DEFAULT false,
 PRIMARY KEY(room_id,member_id));
