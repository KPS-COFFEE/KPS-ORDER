CREATE TABLE driver_evidence (
 id text PRIMARY KEY,brand_id text NOT NULL REFERENCES brands(id),shift_id text NOT NULL REFERENCES shifts(id),
 driver_id text NOT NULL REFERENCES users(id),content_type text NOT NULL,data bytea NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE vehicle_reports ADD COLUMN shift_id text REFERENCES shifts(id);
ALTER TABLE vehicle_reports ADD COLUMN order_id text REFERENCES orders(id);
ALTER TABLE vehicle_reports ADD COLUMN evidence_id text REFERENCES driver_evidence(id);
ALTER TABLE vehicle_reports ADD COLUMN department text NOT NULL DEFAULT 'maintenance' CHECK(department IN ('maintenance','delivery'));
ALTER TABLE vehicle_reports DROP CONSTRAINT vehicle_reports_category_check;
ALTER TABLE vehicle_reports ADD CONSTRAINT vehicle_reports_category_check CHECK(category IN ('engine','transmission','brakes','tyres','battery','electrical','cooling','air_conditioning','oil_leak','accident','scheduled_service','other','order_problem','delay','customer_problem','payment_problem','branch_problem','general'));
