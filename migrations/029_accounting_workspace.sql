CREATE TABLE accounting_reviews (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id),
 report_id text NOT NULL UNIQUE REFERENCES closure_reports(id),
 reviewer_id text NOT NULL REFERENCES users(id), note text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER accounting_reviews_immutable BEFORE UPDATE OR DELETE ON accounting_reviews
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TABLE accounting_cases (
 id text PRIMARY KEY, case_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 brand_id text NOT NULL REFERENCES brands(id), branch_id text NOT NULL REFERENCES branches(id),
 subject_id text NOT NULL REFERENCES users(id), order_id text REFERENCES orders(id),
 vehicle_report_id text REFERENCES vehicle_reports(id),
 category text NOT NULL CHECK(category IN ('order','traffic','fault','damage','other')),
 occurred_on date NOT NULL, vehicle_plate text NOT NULL DEFAULT '', description text NOT NULL,
 amount_halalas bigint NOT NULL DEFAULT 0 CHECK(amount_halalas>=0),
 status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','resolved')),
 responsibility text NOT NULL DEFAULT 'pending' CHECK(responsibility IN ('pending','confirmed','not_responsible')),
 action text NOT NULL DEFAULT '', created_by text NOT NULL REFERENCES users(id),
 version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounting_cases_scope ON accounting_cases(brand_id,branch_id,occurred_on);
CREATE TABLE accounting_case_events (
 id text PRIMARY KEY, case_id text NOT NULL REFERENCES accounting_cases(id),
 actor_id text NOT NULL REFERENCES users(id), details jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER accounting_case_events_immutable BEFORE UPDATE OR DELETE ON accounting_case_events
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TABLE accounting_case_files (
 id text PRIMARY KEY, case_id text NOT NULL REFERENCES accounting_cases(id), uploader_id text NOT NULL REFERENCES users(id),
 content_type text NOT NULL, data bytea NOT NULL, sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE accounting_months (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id), month date NOT NULL,
 branch_ids jsonb NOT NULL, scope_hash text NOT NULL, snapshot jsonb NOT NULL,
 closed_by text NOT NULL REFERENCES users(id), reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(brand_id,month,scope_hash)
);
CREATE TRIGGER accounting_months_immutable BEFORE UPDATE OR DELETE ON accounting_months
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
