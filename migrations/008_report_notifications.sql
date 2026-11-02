-- V5 presentation versions reference an immutable financial snapshot; no ledger copies.
CREATE TABLE report_renderings (
 report_id TEXT NOT NULL REFERENCES closure_reports(id), template_version INT NOT NULL,
 view_kind TEXT NOT NULL CHECK(view_kind IN ('compact','owner')),
 data_version INT NOT NULL, source_sha256 TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','failed')),
 html_data TEXT, pdf_data BYTEA, csv_data BYTEA, error TEXT,
 attempts INT NOT NULL DEFAULT 0, next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), generated_at TIMESTAMPTZ,
 PRIMARY KEY(report_id,template_version,view_kind)
);
CREATE TABLE report_notifications (
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES closure_reports(id),
 recipient_user_id TEXT NOT NULL REFERENCES users(id), brand_id TEXT NOT NULL REFERENCES brands(id),
 template_version INT NOT NULL, status TEXT NOT NULL DEFAULT 'approved_final' CHECK(status='approved_final'),
 email_outbox_id TEXT REFERENCES email_outbox(id), read_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(report_id,recipient_user_id,template_version)
);
CREATE INDEX report_notifications_inbox ON report_notifications(recipient_user_id,created_at DESC);
CREATE INDEX report_renderings_retry ON report_renderings(status,next_attempt_at);
CREATE OR REPLACE FUNCTION protect_saved_report_rendering() RETURNS trigger AS $$ BEGIN
 IF TG_OP='DELETE' OR NEW.report_id IS DISTINCT FROM OLD.report_id OR NEW.template_version IS DISTINCT FROM OLD.template_version
 OR NEW.view_kind IS DISTINCT FROM OLD.view_kind OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
 OR NEW.data_version IS DISTINCT FROM OLD.data_version
 OR (OLD.status='ready' AND (NEW.html_data IS DISTINCT FROM OLD.html_data OR NEW.pdf_data IS DISTINCT FROM OLD.pdf_data OR NEW.csv_data IS DISTINCT FROM OLD.csv_data OR NEW.status IS DISTINCT FROM OLD.status))
 THEN RAISE EXCEPTION 'Immutable saved report rendering'; END IF; RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_saved_report_rendering BEFORE UPDATE OR DELETE ON report_renderings FOR EACH ROW EXECUTE FUNCTION protect_saved_report_rendering();
CREATE OR REPLACE FUNCTION protect_saved_report_files() RETURNS trigger AS $$ BEGIN
 IF OLD.pdf_status='ready' AND (NEW.pdf_data IS DISTINCT FROM OLD.pdf_data OR NEW.csv_data IS DISTINCT FROM OLD.csv_data OR NEW.pdf_status IS DISTINCT FROM OLD.pdf_status)
 THEN RAISE EXCEPTION 'Immutable approved report files'; END IF; RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_approved_report_files BEFORE UPDATE ON closure_reports FOR EACH ROW EXECUTE FUNCTION protect_saved_report_files();
