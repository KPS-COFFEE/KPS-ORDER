-- Existing historical requests retain an explicitly undocumented deadline.
-- New requests and subsequent recorded handoffs receive a deadline in the command.
ALTER TABLE financial_case_clarifications ADD COLUMN due_at timestamptz;
CREATE INDEX financial_clarification_due ON financial_case_clarifications(due_at)
 WHERE status <> 'resolved';
ALTER TABLE financial_case_clarification_events ADD COLUMN due_at timestamptz;
