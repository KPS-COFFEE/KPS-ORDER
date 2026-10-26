-- Responsibility metadata only. Source financial documents remain unchanged.
ALTER TABLE financial_case_clarifications
 ADD COLUMN current_reviewer_id text REFERENCES users(id),
 ADD COLUMN fallback_reviewer_id text REFERENCES users(id);
-- Historical requests retain their original requester; record an eligible named
-- fallback without inventing acceptance or moving any financial responsibility.
UPDATE financial_case_clarifications q SET fallback_reviewer_id=(
 SELECT u.id FROM users u JOIN orders o ON o.id=q.order_id
 WHERE u.active AND u.id<>q.requested_by AND u.brand_id=o.brand_id
 AND (u.role='owner' OR u.role='admin' AND u.permissions ? 'accounting_review' AND u.branch_ids ? o.branch_id)
 ORDER BY CASE WHEN u.role='admin' THEN 0 ELSE 1 END,u.id LIMIT 1
);
ALTER TABLE financial_case_clarification_events
 DROP CONSTRAINT financial_case_clarification_events_action_check;
ALTER TABLE financial_case_clarification_events
 ADD CONSTRAINT financial_case_clarification_events_action_check CHECK(action IN
 ('requested','accepted','responded','declined','returned','resolved','reassigned','reviewer_taken_over'));
