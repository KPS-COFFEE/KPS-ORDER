-- Factual assignments and financial case evidence are append-only. This does
-- not execute a task, approve money or modify any existing historical record.
CREATE FUNCTION protect_accountable_followup_history() RETURNS trigger AS $$
BEGIN
 IF OLD.action LIKE 'employee.ticket.%' THEN
  RAISE EXCEPTION 'Employee follow-up history is append-only';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER employee_followup_history_immutable BEFORE UPDATE OR DELETE ON audit_events
 FOR EACH ROW EXECUTE FUNCTION protect_accountable_followup_history();
CREATE FUNCTION protect_financial_case_history() RETURNS trigger AS $$
BEGIN
 IF OLD.event_type LIKE 'financial_case.%' OR OLD.event_type LIKE 'financial_clarification.%' THEN
  RAISE EXCEPTION 'Financial case evidence is append-only';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER financial_case_history_immutable BEFORE UPDATE OR DELETE ON order_events
 FOR EACH ROW EXECUTE FUNCTION protect_financial_case_history();
