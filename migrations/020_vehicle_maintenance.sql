-- Additive vehicle reports, independent of delivery tasks, shifts and money.
-- No staff invitations, activation or permission grants happen in migrations.
CREATE TABLE vehicle_reports (
 id TEXT PRIMARY KEY,
 brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_id TEXT NOT NULL REFERENCES branches(id),
 driver_id TEXT NOT NULL REFERENCES users(id),
 category TEXT NOT NULL CHECK(category IN ('engine','transmission','brakes','tyres','battery','electrical','cooling','air_conditioning','oil_leak','accident','scheduled_service','other')),
 vehicle_description TEXT NOT NULL CHECK(length(btrim(vehicle_description)) BETWEEN 1 AND 150),
 vehicle_plate TEXT NOT NULL DEFAULT '' CHECK(length(vehicle_plate)<=40),
 description TEXT NOT NULL CHECK(length(btrim(description)) BETWEEN 1 AND 2000),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','resolved')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 resolution_note TEXT NOT NULL DEFAULT '' CHECK(length(resolution_note)<=2000),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 resolved_at TIMESTAMPTZ,
 CHECK((status='resolved')=(resolved_at IS NOT NULL))
);
CREATE INDEX vehicle_reports_scope ON vehicle_reports(brand_id,branch_id,created_at DESC,id);
CREATE INDEX vehicle_reports_driver ON vehicle_reports(driver_id,created_at DESC,id);
CREATE TABLE vehicle_report_events (
 id TEXT PRIMARY KEY,
 report_id TEXT NOT NULL REFERENCES vehicle_reports(id),
 actor_id TEXT NOT NULL REFERENCES users(id),
 event_type TEXT NOT NULL CHECK(event_type IN ('created','in_progress','resolved','reopened')),
 details JSONB NOT NULL DEFAULT '{}'::jsonb,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX vehicle_report_events_report ON vehicle_report_events(report_id,created_at,id);
