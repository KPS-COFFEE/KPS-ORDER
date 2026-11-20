-- Approved tender reclassification overlays immutable original collections.
-- Amount/invoice/old closure snapshots never change; custody adjustments are
-- linked reclassifications, not new customer receipts or bank confirmations.
CREATE TABLE collection_tender_correction_proposals (
 id text PRIMARY KEY, collection_id text NOT NULL REFERENCES collections(id), order_id text NOT NULL REFERENCES orders(id),
 brand_id text NOT NULL REFERENCES brands(id), branch_id text NOT NULL REFERENCES branches(id),
 expected_order_version integer NOT NULL CHECK(expected_order_version>=1), source_correction_id text,
 source_method text NOT NULL CHECK(source_method IN ('cash','card')), target_method text NOT NULL CHECK(target_method IN ('cash','card')),
 amount_halalas bigint NOT NULL CHECK(amount_halalas>0),
 target_tendered_halalas bigint, target_change_halalas bigint NOT NULL CHECK(target_change_halalas>=0), target_reference text NOT NULL,
 evidence_reference text NOT NULL CHECK(length(btrim(evidence_reference))>=5), note text NOT NULL CHECK(length(btrim(note))>=5),
 source_snapshot jsonb NOT NULL, requested_by text NOT NULL REFERENCES users(id),
 followup_user_id text NOT NULL REFERENCES users(id), due_on date NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(source_method<>target_method),
 CHECK((target_method='cash' AND target_tendered_halalas IS NOT NULL AND target_tendered_halalas>=amount_halalas AND target_change_halalas=target_tendered_halalas-amount_halalas)
    OR (target_method='card' AND target_tendered_halalas IS NULL AND target_change_halalas=0 AND length(btrim(target_reference))>0))
);
CREATE TABLE collection_tender_corrections (
 id text PRIMARY KEY, proposal_id text NOT NULL UNIQUE REFERENCES collection_tender_correction_proposals(id),
 collection_id text NOT NULL REFERENCES collections(id), order_id text NOT NULL REFERENCES orders(id),
 brand_id text NOT NULL REFERENCES brands(id), branch_id text NOT NULL REFERENCES branches(id),
 source_correction_id text REFERENCES collection_tender_corrections(id) DEFERRABLE INITIALLY DEFERRED, revision integer NOT NULL CHECK(revision>=1),
 effective_shift_id text NOT NULL REFERENCES shifts(id), amount_halalas bigint NOT NULL CHECK(amount_halalas>0),
 source_method text NOT NULL CHECK(source_method IN ('cash','card')), target_method text NOT NULL CHECK(target_method IN ('cash','card')),
 target_tendered_halalas bigint, target_change_halalas bigint NOT NULL CHECK(target_change_halalas>=0), target_reference text NOT NULL,
 cash_delta_halalas bigint NOT NULL CHECK(cash_delta_halalas<>0), custody_event_id text NOT NULL UNIQUE REFERENCES custody_events(id),
 evidence_reference text NOT NULL CHECK(length(btrim(evidence_reference))>=5), note text NOT NULL CHECK(length(btrim(note))>=5),
 approved_by text NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(collection_id,revision),
 CHECK(source_method<>target_method),
 CHECK(cash_delta_halalas=CASE WHEN target_method='cash' THEN amount_halalas ELSE -amount_halalas END),
 CHECK((target_method='cash' AND target_tendered_halalas IS NOT NULL AND target_tendered_halalas>=amount_halalas AND target_change_halalas=target_tendered_halalas-amount_halalas)
    OR (target_method='card' AND target_tendered_halalas IS NULL AND target_change_halalas=0 AND length(btrim(target_reference))>0))
);
ALTER TABLE collection_tender_correction_proposals ADD CONSTRAINT tender_proposal_source_fk FOREIGN KEY(source_correction_id) REFERENCES collection_tender_corrections(id) DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX tender_no_supersession_fork ON collection_tender_corrections(source_correction_id) WHERE source_correction_id IS NOT NULL;
CREATE TABLE collection_tender_correction_rejections (
 proposal_id text PRIMARY KEY REFERENCES collection_tender_correction_proposals(id),
 rejected_by text NOT NULL REFERENCES users(id), note text NOT NULL CHECK(length(btrim(note))>=5), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_tender_proposals BEFORE UPDATE OR DELETE ON collection_tender_correction_proposals FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER immutable_tender_corrections BEFORE UPDATE OR DELETE ON collection_tender_corrections FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER immutable_tender_rejections BEFORE UPDATE OR DELETE ON collection_tender_correction_rejections FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE INDEX tender_correction_source ON collection_tender_corrections(collection_id,revision DESC);

CREATE OR REPLACE VIEW effective_collections AS
 SELECT p.id,p.order_id,coalesce(a.target_shift_id,p.shift_id) AS shift_id,
 coalesce(a.target_driver_id,p.collector_id) AS collector_id,coalesce(t.target_method,p.method) AS method,p.amount_halalas,
 CASE WHEN t.id IS NOT NULL THEN t.target_tendered_halalas ELSE p.tendered_halalas END AS tendered_halalas,
 coalesce(t.target_change_halalas,p.change_halalas) AS change_halalas,
 coalesce(t.target_reference,p.reference) AS reference,p.created_at,p.amendment_revision
 FROM collections p LEFT JOIN order_driver_attribution_corrections a ON a.order_id=p.order_id
 LEFT JOIN LATERAL (SELECT * FROM collection_tender_corrections t WHERE t.collection_id=p.id ORDER BY revision DESC LIMIT 1) t ON true;

-- Rollback/older images cannot save a new driver snapshot using uncorrected
-- tender facts. Existing historical snapshots remain byte-for-byte immutable.
CREATE FUNCTION protect_corrected_tender_snapshot() RETURNS trigger AS $$
DECLARE expected jsonb; observed jsonb;
BEGIN
 IF NEW.kind='driver' AND EXISTS(SELECT 1 FROM collection_tender_corrections WHERE effective_shift_id=NEW.shift_id) THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('id',p.id,'method',p.method,'amount_halalas',p.amount_halalas,'tendered_halalas',p.tendered_halalas,'change_halalas',p.change_halalas) ORDER BY p.id),'[]'::jsonb)
    INTO expected FROM effective_collections p WHERE p.shift_id=NEW.shift_id;
  SELECT coalesce(jsonb_agg(jsonb_build_object('id',p->>'id','method',p->>'method','amount_halalas',(p->>'amount_halalas')::bigint,'tendered_halalas',(p->>'tendered_halalas')::bigint,'change_halalas',(p->>'change_halalas')::bigint) ORDER BY p->>'id'),'[]'::jsonb)
    INTO observed FROM jsonb_array_elements(coalesce(NEW.snapshot->'collections','[]'::jsonb)) p;
  IF observed IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Corrected collection tender requires compatible closure snapshot'; END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER corrected_tender_snapshot_compatibility BEFORE INSERT ON closure_reports FOR EACH ROW EXECUTE FUNCTION protect_corrected_tender_snapshot();
