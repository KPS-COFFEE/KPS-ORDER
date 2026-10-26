ALTER TABLE shared_manager_closure_declarations ALTER COLUMN recipient_user_id DROP NOT NULL;
-- Operational duty may end without inventing a cash receipt or financial approval.
CREATE TABLE manager_operational_releases (
 workday_id text PRIMARY KEY REFERENCES central_workdays(id),
 declaration_id text NOT NULL REFERENCES shared_manager_closure_declarations(id),
 recorded_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_manager_operational_release BEFORE UPDATE OR DELETE
 ON manager_operational_releases FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
-- Operational staffing ends on witnessed cash receipt. A short/ambiguous float
-- requires a durable assigned review, not a fabricated top-up or employee debt.
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
  IF NEW.kind='manager' AND EXISTS (
    SELECT 1 FROM central_workday_legs l
    JOIN central_workdays w ON w.id=l.workday_id
    JOIN manager_operational_releases e ON e.workday_id=w.id
    JOIN shared_manager_closure_declarations d ON d.id=e.declaration_id AND d.workday_id=w.id
    WHERE l.shift_id=NEW.id AND w.user_id=NEW.user_id
      AND e.created_at=NEW.staffing_ended_at AND e.recorded_by=w.user_id
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
