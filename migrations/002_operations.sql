CREATE TABLE IF NOT EXISTS shifts (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('driver','manager')), user_id TEXT NOT NULL REFERENCES users(id),
 brand_id TEXT NOT NULL REFERENCES brands(id), branch_ids JSONB NOT NULL, manager_shift_id TEXT REFERENCES shifts(id),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closing','submitted','closed')),
 opening_halalas BIGINT NOT NULL DEFAULT 0 CHECK(opening_halalas>=0), opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 submitted_at TIMESTAMPTZ, closed_at TIMESTAMPTZ, submission JSONB, approved_by TEXT REFERENCES users(id), exception_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS shifts_one_active_user ON shifts(user_id) WHERE status<>'closed';
CREATE INDEX IF NOT EXISTS shifts_manager_children ON shifts(manager_shift_id,status);
CREATE TABLE IF NOT EXISTS driver_tasks (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), driver_id TEXT NOT NULL REFERENCES users(id), shift_id TEXT NOT NULL REFERENCES shifts(id),
 status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','accepted','picked_up','problem','delivered','cancelled','transferred')),
 assigned_by TEXT NOT NULL REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), accepted_at TIMESTAMPTZ,
 picked_up_at TIMESTAMPTZ, delivered_at TIMESTAMPTZ, proof TEXT, reason TEXT,
 transfer_shift_id TEXT REFERENCES shifts(id), transfer_requested_by TEXT REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS driver_tasks_one_active_order ON driver_tasks(order_id) WHERE status NOT IN ('delivered','cancelled','transferred');
CREATE INDEX IF NOT EXISTS driver_tasks_shift ON driver_tasks(shift_id,status);
CREATE TABLE IF NOT EXISTS collections (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES orders(id), shift_id TEXT REFERENCES shifts(id), collector_id TEXT NOT NULL REFERENCES users(id),
 method TEXT NOT NULL CHECK(method IN ('cash','card')), amount_halalas BIGINT NOT NULL CHECK(amount_halalas>0),
 tendered_halalas BIGINT, change_halalas BIGINT NOT NULL DEFAULT 0, reference TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS refunds (
 id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES collections(id), order_id TEXT NOT NULL REFERENCES orders(id),
 shift_id TEXT REFERENCES shifts(id), method TEXT NOT NULL CHECK(method IN ('cash','card')), amount_halalas BIGINT NOT NULL CHECK(amount_halalas>0),
 line_quantities JSONB NOT NULL DEFAULT '{}', reason TEXT NOT NULL, authorized_by TEXT NOT NULL REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS refunds_order ON refunds(order_id);
CREATE TABLE IF NOT EXISTS custody_transfers (
 id TEXT PRIMARY KEY, from_shift_id TEXT NOT NULL REFERENCES shifts(id), to_shift_id TEXT REFERENCES shifts(id),
 recipient_user_id TEXT NOT NULL REFERENCES users(id), amount_halalas BIGINT NOT NULL CHECK(amount_halalas>=0),
 reference TEXT NOT NULL, closure BOOL NOT NULL DEFAULT false, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','rejected')),
 created_by TEXT NOT NULL REFERENCES users(id), confirmed_by TEXT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), confirmed_at TIMESTAMPTZ,
 CHECK (to_shift_id IS NULL OR from_shift_id<>to_shift_id)
);
CREATE TABLE IF NOT EXISTS custody_events (
 id TEXT PRIMARY KEY, shift_id TEXT NOT NULL REFERENCES shifts(id), amount_halalas BIGINT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('collection','refund','transfer_in','transfer_out','expense','direct_receipt','adjustment')),
 reference_id TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(shift_id,kind,reference_id)
);
CREATE TABLE IF NOT EXISTS closure_reports (
 id TEXT PRIMARY KEY, shift_id TEXT NOT NULL UNIQUE REFERENCES shifts(id), kind TEXT NOT NULL, brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_ids JSONB NOT NULL, user_id TEXT NOT NULL REFERENCES users(id), version INT NOT NULL DEFAULT 1,
 snapshot JSONB NOT NULL, pdf_status TEXT NOT NULL DEFAULT 'pending' CHECK(pdf_status IN ('pending','ready','failed')),
 pdf_error TEXT, pdf_data BYTEA, csv_data BYTEA, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES orders(id), brand_id TEXT NOT NULL REFERENCES brands(id), branch_id TEXT NOT NULL REFERENCES branches(id),
 customer_id TEXT NOT NULL REFERENCES users(id), route TEXT NOT NULL CHECK(route IN ('delivery','branch')),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','resolved')), assignee_id TEXT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chat_attachments (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), uploader_id TEXT NOT NULL REFERENCES users(id),
 content_type TEXT NOT NULL, size_bytes INT NOT NULL CHECK(size_bytes>0 AND size_bytes<=5242880), sha256 TEXT NOT NULL, data BYTEA NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chat_messages (
 id TEXT PRIMARY KEY, seq BIGSERIAL UNIQUE, conversation_id TEXT NOT NULL REFERENCES conversations(id), sender_id TEXT NOT NULL REFERENCES users(id),
 text TEXT NOT NULL DEFAULT '', attachment_id TEXT REFERENCES chat_attachments(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK (length(text)<=4000 AND (length(text)>0 OR attachment_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS chat_messages_conversation ON chat_messages(conversation_id,seq);
CREATE TABLE IF NOT EXISTS conversation_reads (
 conversation_id TEXT NOT NULL REFERENCES conversations(id), user_id TEXT NOT NULL REFERENCES users(id), through_seq BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(conversation_id,user_id)
);
CREATE TABLE IF NOT EXISTS conversation_transfers (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), from_route TEXT NOT NULL, to_route TEXT NOT NULL,
 reason TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION prevent_financial_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'Immutable financial record'; END; $$ LANGUAGE plpgsql;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_collections' AND tgrelid='collections'::regclass) THEN CREATE TRIGGER immutable_collections BEFORE UPDATE OR DELETE ON collections FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation(); END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_refunds' AND tgrelid='refunds'::regclass) THEN CREATE TRIGGER immutable_refunds BEFORE UPDATE OR DELETE ON refunds FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation(); END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_custody_events' AND tgrelid='custody_events'::regclass) THEN CREATE TRIGGER immutable_custody_events BEFORE UPDATE OR DELETE ON custody_events FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation(); END IF;
END $$;
CREATE OR REPLACE FUNCTION protect_report_snapshot() RETURNS trigger AS $$ BEGIN
 IF TG_OP='DELETE' OR NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.shift_id IS DISTINCT FROM OLD.shift_id OR NEW.version IS DISTINCT FROM OLD.version THEN RAISE EXCEPTION 'Immutable report snapshot'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_report_snapshot' AND tgrelid='closure_reports'::regclass) THEN CREATE TRIGGER immutable_report_snapshot BEFORE UPDATE OR DELETE ON closure_reports FOR EACH ROW EXECUTE FUNCTION protect_report_snapshot(); END IF; END $$;
CREATE TABLE IF NOT EXISTS retained_carryovers (
 id TEXT PRIMARY KEY, from_shift_id TEXT NOT NULL UNIQUE REFERENCES shifts(id), user_id TEXT NOT NULL REFERENCES users(id),
 amount_halalas BIGINT NOT NULL CHECK(amount_halalas>0), approved_by TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
 to_shift_id TEXT UNIQUE REFERENCES shifts(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), carried_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS manager_handovers (
 id TEXT PRIMARY KEY, from_shift_id TEXT NOT NULL REFERENCES shifts(id), to_shift_id TEXT NOT NULL REFERENCES shifts(id),
 branch_ids JSONB NOT NULL, driver_shift_ids JSONB NOT NULL, reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
 requested_by TEXT NOT NULL REFERENCES users(id), accepted_by TEXT REFERENCES users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), accepted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS manager_handovers_pending_source ON manager_handovers(from_shift_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS report_corrections (
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES closure_reports(id), correction_version INT NOT NULL,
 shift_id TEXT NOT NULL REFERENCES shifts(id), event_reference_id TEXT NOT NULL, amount_halalas BIGINT NOT NULL,
 reason TEXT NOT NULL, authorized_by TEXT NOT NULL REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(report_id,correction_version)
);
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_report_corrections' AND tgrelid='report_corrections'::regclass) THEN CREATE TRIGGER immutable_report_corrections BEFORE UPDATE OR DELETE ON report_corrections FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation(); END IF; END $$;
CREATE OR REPLACE FUNCTION protect_final_operational_record() RETURNS trigger AS $$ BEGIN
 IF (TG_TABLE_NAME='shifts' AND OLD.status='closed') OR (TG_TABLE_NAME='driver_tasks' AND OLD.status IN ('delivered','cancelled','transferred')) OR (TG_TABLE_NAME='custody_transfers' AND OLD.status='confirmed') OR (TG_TABLE_NAME='manager_handovers' AND OLD.status='accepted') THEN RAISE EXCEPTION 'Final operational record is immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_closed_shift' AND tgrelid='shifts'::regclass) THEN CREATE TRIGGER immutable_closed_shift BEFORE UPDATE OR DELETE ON shifts FOR EACH ROW EXECUTE FUNCTION protect_final_operational_record(); END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_final_task' AND tgrelid='driver_tasks'::regclass) THEN CREATE TRIGGER immutable_final_task BEFORE UPDATE OR DELETE ON driver_tasks FOR EACH ROW EXECUTE FUNCTION protect_final_operational_record(); END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_confirmed_custody' AND tgrelid='custody_transfers'::regclass) THEN CREATE TRIGGER immutable_confirmed_custody BEFORE UPDATE OR DELETE ON custody_transfers FOR EACH ROW EXECUTE FUNCTION protect_final_operational_record(); END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_accepted_manager_handover' AND tgrelid='manager_handovers'::regclass) THEN CREATE TRIGGER immutable_accepted_manager_handover BEFORE UPDATE OR DELETE ON manager_handovers FOR EACH ROW EXECUTE FUNCTION protect_final_operational_record(); END IF;
END $$;
