-- New manual refund execution attestations; never invent proof for historical rows.
ALTER TABLE refunds ADD COLUMN IF NOT EXISTS execution_evidence JSONB;
ALTER TABLE refunds ADD CONSTRAINT refunds_execution_evidence_valid CHECK (
 execution_evidence IS NULL OR (
  jsonb_typeof(execution_evidence)='object'
  AND execution_evidence->>'evidence_kind'='manual_execution_attestation'
  AND execution_evidence->'executed_confirmed'='true'::jsonb
  AND execution_evidence->>'recorded_by'=authorized_by
  AND jsonb_typeof(execution_evidence->'reference')='string'
  AND length(btrim(execution_evidence->>'reference')) BETWEEN 3 AND 500
 ) IS TRUE
);
