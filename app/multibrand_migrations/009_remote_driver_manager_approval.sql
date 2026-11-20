-- Final remote duty approval is separate from physical cash receipt. Existing
-- immutable end markers remain unchanged, including historical branch deposits.
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
    SELECT 1 FROM central_workday_legs l
    JOIN central_workdays w ON w.id=l.workday_id
    JOIN shared_driver_closure_declarations d ON d.workday_id=w.id
    JOIN audit_events e ON e.entity_type='central_workday' AND e.entity_id=w.id
      AND e.action='shared_driver.manager_approved'
    JOIN users approver ON approver.id=e.actor_id
    JOIN shifts parent ON parent.id=NEW.manager_shift_id
    WHERE l.shift_id=NEW.id AND w.user_id=NEW.user_id
      AND d.id=NEW.submission->>'shared_declaration_id'
      AND d.id=NEW.submission->'manager_approval'->>'declaration_id'
      AND d.id=(SELECT newest.id FROM shared_driver_closure_declarations newest WHERE newest.workday_id=w.id ORDER BY newest.revision DESC LIMIT 1)
      AND e.actor_id=NEW.submission->'manager_approval'->>'approved_by'
      AND e.actor_id=parent.user_id AND e.actor_id<>NEW.user_id
      AND approver.role='delivery_manager' AND approver.active
      AND e.details @> (NEW.submission->'manager_approval')
      AND e.details->>'submitted_by'=d.recorded_by
      AND e.details->>'submission_reason'=d.reason
      AND e.details->>'delegated_submission'=CASE WHEN d.recorded_by<>NEW.user_id THEN 'true' ELSE 'false' END
      AND (d.recorded_by=NEW.user_id OR (d.recorded_by=e.actor_id AND length(btrim(d.reason))>0))
      AND e.details->>'cash_receipt_recorded'='false'
      AND (e.details->>'approved_at')::timestamptz=NEW.staffing_ended_at
      AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(d.expected_snapshot) j WHERE j->>'manager_user_id'<>e.actor_id)
      AND NOT EXISTS (SELECT 1 FROM shared_driver_closure_returns returned WHERE returned.declaration_id=d.id)
      AND (EXISTS (SELECT 1 FROM driver_cash_bags b WHERE b.declaration_id=d.id AND b.driver_id=NEW.user_id AND b.destination='branch')
        OR EXISTS (SELECT 1 FROM shared_driver_closure_receipts r WHERE r.declaration_id=d.id))
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
      AND coalesce(NEW.submission->>'manager_approval_required','false')<>'true'
      AND NOT EXISTS (SELECT 1 FROM driver_cash_bags b WHERE b.declaration_id=d.id AND b.destination='branch')
      AND NOT EXISTS (SELECT 1 FROM branches br WHERE NEW.branch_ids ? br.id
        AND regexp_replace(coalesce(br.city,''),'[[:space:]]','','g')<>'حفرالباطن')
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
