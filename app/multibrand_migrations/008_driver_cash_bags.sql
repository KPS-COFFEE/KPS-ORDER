-- Separate sealed driver bags; counting never rewrites declarations or ledgers.
CREATE TABLE driver_cash_bags (
 id text PRIMARY KEY, workday_id text NOT NULL REFERENCES central_workdays(id),
 declaration_id text NOT NULL UNIQUE REFERENCES shared_driver_closure_declarations(id),
 driver_id text NOT NULL REFERENCES users(id), branch_id text NOT NULL REFERENCES branches(id),
 destination text NOT NULL CHECK(destination IN ('manager_vault','branch')),
 reference text NOT NULL CHECK(length(reference) BETWEEN 1 AND 200),
 declared_cash_halalas bigint NOT NULL CHECK(declared_cash_halalas>=0),
 declared_card_halalas bigint NOT NULL CHECK(declared_card_halalas>=0),
 recorded_by text NOT NULL REFERENCES users(id), deposited_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE driver_cash_bag_counts (
 id text PRIMARY KEY, bag_id text NOT NULL UNIQUE REFERENCES driver_cash_bags(id),
 received_cash_halalas bigint NOT NULL CHECK(received_cash_halalas>=0),
 card_halalas bigint NOT NULL CHECK(card_halalas>=0), counted_by text NOT NULL REFERENCES users(id),
 reference text NOT NULL CHECK(length(reference) BETWEEN 1 AND 200),
 note text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE manager_vault_closures (
 id text PRIMARY KEY, workday_id text NOT NULL UNIQUE REFERENCES central_workdays(id),
 manager_id text NOT NULL REFERENCES users(id), bag_ids jsonb NOT NULL,
 note text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_driver_cash_bag BEFORE UPDATE OR DELETE ON driver_cash_bags FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER immutable_driver_cash_bag_count BEFORE UPDATE OR DELETE ON driver_cash_bag_counts FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER immutable_manager_vault_closure BEFORE UPDATE OR DELETE ON manager_vault_closures FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE OR REPLACE FUNCTION guard_staffing_end_marker() RETURNS trigger AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.staffing_ended_at IS NOT NULL THEN RAISE EXCEPTION 'New shift cannot start physically finished'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.staffing_ended_at IS DISTINCT FROM OLD.staffing_ended_at THEN
  IF OLD.staffing_ended_at IS NOT NULL OR NEW.staffing_ended_at IS NULL OR NEW.status NOT IN ('submitted','closed') THEN
   RAISE EXCEPTION 'Staffing end requires immutable physical receipt';
  END IF;
  IF NEW.kind='driver' AND EXISTS (
    SELECT 1 FROM driver_cash_bags b JOIN central_workday_legs l ON l.workday_id=b.workday_id
    WHERE l.shift_id=NEW.id AND b.driver_id=NEW.user_id AND b.destination='branch' AND b.deposited_at=NEW.staffing_ended_at
  ) THEN RETURN NEW; END IF;
  IF NEW.kind='manager' AND EXISTS (
    SELECT 1 FROM manager_vault_closures v JOIN central_workday_legs l ON l.workday_id=v.workday_id
    WHERE l.shift_id=NEW.id AND v.manager_id=NEW.user_id AND v.created_at=NEW.staffing_ended_at
  ) THEN RETURN NEW; END IF;
  IF NEW.kind='driver' AND EXISTS (
    SELECT 1 FROM central_workday_legs l JOIN shared_driver_closure_declarations d ON d.workday_id=l.workday_id
    JOIN shared_driver_closure_receipts r ON r.declaration_id=d.id
    LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id
    WHERE l.shift_id=NEW.id AND r.created_at=NEW.staffing_ended_at
      AND NOT EXISTS(SELECT 1 FROM shared_driver_closure_returns returned WHERE returned.declaration_id=d.id)
      AND (a.id IS NOT NULL OR d.retained_halalas >= (SELECT COALESCE(sum((j->>'permanent_float_halalas')::bigint),0) FROM jsonb_array_elements(d.expected_snapshot) j)
        OR EXISTS(SELECT 1 FROM audit_events e JOIN users u ON u.id=e.details->>'responsible_user_id'
          WHERE e.action='shared_driver.float_followup' AND e.entity_type='central_workday' AND e.entity_id=d.workday_id
            AND e.details->>'declaration_id'=d.id AND e.details->>'receipt_id'=r.id
            AND e.details->>'due_at' IS NOT NULL AND e.details->>'no_employee_liability_assumed'='true'
            AND (e.details->>'declared_retained_halalas')::bigint=d.retained_halalas))
  ) THEN RETURN NEW; END IF;
  IF NEW.kind='manager' AND EXISTS (
    SELECT 1 FROM central_workday_legs l JOIN shared_manager_closure_declarations d ON d.workday_id=l.workday_id
    JOIN shared_manager_closure_receipts r ON r.declaration_id=d.id
    WHERE l.shift_id=NEW.id AND r.created_at=NEW.staffing_ended_at AND r.received_by=d.recipient_user_id
      AND NOT EXISTS(SELECT 1 FROM shared_manager_closure_returns returned WHERE returned.declaration_id=d.id)
  ) THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'Staffing end requires immutable physical receipt and assigned float followup';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
