-- Physical declarations remain distinct from subsequent evidenced brand allocation.
CREATE TABLE shared_driver_closure_declarations (
 id text PRIMARY KEY,
 workday_id text NOT NULL REFERENCES central_workdays(id),
 revision integer NOT NULL CHECK(revision>0),
 cash_halalas bigint NOT NULL CHECK(cash_halalas>=0),
 card_halalas bigint NOT NULL CHECK(card_halalas>=0),
 retained_halalas bigint NOT NULL CHECK(retained_halalas>=0 AND retained_halalas<=cash_halalas),
 expected_snapshot jsonb NOT NULL,
 recorded_by text NOT NULL REFERENCES users(id),
 reason text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workday_id,revision)
);
CREATE TABLE shared_driver_closure_returns (
 declaration_id text PRIMARY KEY REFERENCES shared_driver_closure_declarations(id),
 recorded_by text NOT NULL REFERENCES users(id),
 reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE shared_driver_closure_receipts (
 id text PRIMARY KEY,
 declaration_id text NOT NULL UNIQUE REFERENCES shared_driver_closure_declarations(id),
 received_halalas bigint NOT NULL CHECK(received_halalas>=0),
 received_by text NOT NULL REFERENCES users(id),
 manager_shift_ids jsonb NOT NULL,
 reference text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE shared_driver_closure_allocations (
 id text PRIMARY KEY,
 declaration_id text NOT NULL UNIQUE REFERENCES shared_driver_closure_declarations(id),
 receipt_id text NOT NULL UNIQUE REFERENCES shared_driver_closure_receipts(id),
 legs jsonb NOT NULL,
 recorded_by text NOT NULL REFERENCES users(id),
 reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_shared_driver_declaration BEFORE UPDATE OR DELETE ON shared_driver_closure_declarations FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER immutable_shared_driver_return BEFORE UPDATE OR DELETE ON shared_driver_closure_returns FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER immutable_shared_driver_receipt BEFORE UPDATE OR DELETE ON shared_driver_closure_receipts FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER immutable_shared_driver_allocation BEFORE UPDATE OR DELETE ON shared_driver_closure_allocations FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
ALTER TABLE shifts ADD COLUMN staffing_ended_at timestamptz;
CREATE FUNCTION guard_staffing_end_marker() RETURNS trigger AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.staffing_ended_at IS NOT NULL THEN RAISE EXCEPTION 'New shift cannot start physically finished'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.staffing_ended_at IS DISTINCT FROM OLD.staffing_ended_at THEN
  IF OLD.staffing_ended_at IS NOT NULL OR NEW.staffing_ended_at IS NULL OR NEW.kind<>'driver' OR NEW.status NOT IN ('submitted','closed') OR NOT EXISTS (
    SELECT 1 FROM central_workday_legs l
    JOIN shared_driver_closure_declarations d ON d.workday_id=l.workday_id
    JOIN shared_driver_closure_receipts r ON r.declaration_id=d.id
    LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id
    WHERE l.shift_id=NEW.id AND r.created_at=NEW.staffing_ended_at
      AND (a.id IS NOT NULL OR d.retained_halalas >= (SELECT COALESCE(sum((j->>'permanent_float_halalas')::bigint),0) FROM jsonb_array_elements(d.expected_snapshot) j))
  ) THEN RAISE EXCEPTION 'Staffing end requires immutable physical receipt and evidenced retained float'; END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER guarded_staffing_end BEFORE INSERT OR UPDATE ON shifts FOR EACH ROW EXECUTE FUNCTION guard_staffing_end_marker();
DROP INDEX shifts_one_active_user_brand;
CREATE UNIQUE INDEX shifts_one_active_user_brand ON shifts(user_id,brand_id) WHERE status<>'closed' AND staffing_ended_at IS NULL;
