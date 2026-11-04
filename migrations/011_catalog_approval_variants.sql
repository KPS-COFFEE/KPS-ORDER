-- Owner final decision 2026-10-10: exact historical menu/source scope only.
-- No branch operation data, financial snapshots or source assets are overwritten.
-- Runtime evaluates these frozen digests once after the existing safe seed import.
CREATE TABLE IF NOT EXISTS catalog_owner_approvals (
 decision_id TEXT NOT NULL,product_id TEXT NOT NULL,brand_id TEXT NOT NULL,
 source_commit TEXT NOT NULL,source_html_sha256 TEXT NOT NULL,
 source_digest TEXT NOT NULL,source_snapshot JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),evaluated_at TIMESTAMPTZ,
 result TEXT NOT NULL DEFAULT 'pending' CHECK(result IN ('pending','approved','skipped_mismatch')),
 transform_evaluated_at TIMESTAMPTZ,
 transform_result TEXT CHECK(transform_result IN ('applied','skipped_mismatch')),
 PRIMARY KEY(decision_id,product_id)
);
CREATE OR REPLACE FUNCTION protect_owner_catalog_approval() RETURNS trigger AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Owner menu decision evidence is immutable'; END IF;
 IF NEW.decision_id IS DISTINCT FROM OLD.decision_id OR NEW.product_id IS DISTINCT FROM OLD.product_id
  OR NEW.brand_id IS DISTINCT FROM OLD.brand_id OR NEW.source_commit IS DISTINCT FROM OLD.source_commit
  OR NEW.source_html_sha256 IS DISTINCT FROM OLD.source_html_sha256
  OR NEW.source_digest IS DISTINCT FROM OLD.source_digest OR NEW.source_snapshot IS DISTINCT FROM OLD.source_snapshot
  OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
  RAISE EXCEPTION 'Owner menu source scope is immutable';
 END IF;
 IF OLD.evaluated_at IS NOT NULL AND (NEW.evaluated_at IS DISTINCT FROM OLD.evaluated_at OR NEW.result IS DISTINCT FROM OLD.result) THEN
  RAISE EXCEPTION 'An evaluated menu decision cannot approve future edits';
 END IF;
 IF OLD.transform_evaluated_at IS NOT NULL AND (NEW.transform_evaluated_at IS DISTINCT FROM OLD.transform_evaluated_at OR NEW.transform_result IS DISTINCT FROM OLD.transform_result) THEN
  RAISE EXCEPTION 'An applied menu amendment cannot be replayed';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_owner_catalog_approval BEFORE UPDATE OR DELETE ON catalog_owner_approvals
 FOR EACH ROW EXECUTE FUNCTION protect_owner_catalog_approval();
