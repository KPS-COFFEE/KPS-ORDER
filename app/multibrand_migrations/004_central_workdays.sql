-- An additive grouping of canonical brand ledgers. Physical vehicle work belongs
-- to exactly one anchor; no historical shift/custody/report is rewritten.
CREATE TABLE central_workdays (
 id text PRIMARY KEY,
 user_id text NOT NULL REFERENCES users(id),
 kind text NOT NULL CHECK (kind IN ('manager','driver')),
 anchor_shift_id text NOT NULL UNIQUE REFERENCES shifts(id),
 created_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE central_workday_legs (
 workday_id text NOT NULL REFERENCES central_workdays(id),
 brand_id text NOT NULL REFERENCES brands(id),
 shift_id text NOT NULL UNIQUE,
 PRIMARY KEY (workday_id,brand_id),
 FOREIGN KEY (brand_id,shift_id) REFERENCES shifts(brand_id,id)
);
CREATE INDEX central_workdays_user ON central_workdays(user_id,created_at DESC);
