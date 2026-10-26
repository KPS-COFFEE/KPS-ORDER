-- Responsibility metadata only. Source financial documents remain unchanged.
ALTER TABLE financial_case_clarifications
 ADD COLUMN current_reviewer_id text REFERENCES users(id),
 ADD COLUMN fallback_reviewer_id text REFERENCES users(id);
-- Fallback assignment occurs only through the enabled, authorized workflow.
-- Existing requests retain their original recorded reviewer and no new handoff.
ALTER TABLE financial_case_clarification_events
 DROP CONSTRAINT financial_case_clarification_events_action_check;
ALTER TABLE financial_case_clarification_events
 ADD CONSTRAINT financial_case_clarification_events_action_check CHECK(action IN
 ('requested','accepted','responded','declined','returned','resolved','reassigned','reviewer_taken_over'));
CREATE INDEX financial_clarification_assignee_pending
 ON financial_case_clarifications(assignee_id,status);
