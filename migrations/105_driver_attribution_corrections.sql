-- Correct accounting attribution without rewriting the original task, tender,
-- receipt, invoice, physical work evidence or custody ledger.
CREATE TABLE order_driver_attribution_corrections (
 id text PRIMARY KEY, order_id text NOT NULL UNIQUE REFERENCES orders(id),
 brand_id text NOT NULL REFERENCES brands(id), branch_id text NOT NULL REFERENCES branches(id),
 source_task_id text NOT NULL UNIQUE REFERENCES driver_tasks(id),
 source_shift_id text NOT NULL REFERENCES shifts(id), source_driver_id text NOT NULL REFERENCES users(id),
 target_shift_id text NOT NULL REFERENCES shifts(id), target_driver_id text NOT NULL REFERENCES users(id),
 manager_shift_id text NOT NULL REFERENCES shifts(id),
 expected_version integer NOT NULL CHECK(expected_version>=1),
 cash_halalas bigint NOT NULL CHECK(cash_halalas>=0), card_halalas bigint NOT NULL CHECK(card_halalas>=0),
 actor_id text NOT NULL REFERENCES users(id), reason text NOT NULL CHECK(length(btrim(reason))>=5),
 details jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(source_shift_id<>target_shift_id), CHECK(source_driver_id<>target_driver_id),
 CHECK(cash_halalas+card_halalas>0)
);
CREATE TRIGGER immutable_order_driver_attribution_corrections BEFORE UPDATE OR DELETE
 ON order_driver_attribution_corrections FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE INDEX attribution_source_shift ON order_driver_attribution_corrections(source_shift_id);
CREATE INDEX attribution_target_shift ON order_driver_attribution_corrections(target_shift_id);

CREATE VIEW effective_driver_tasks AS
 SELECT t.id,t.order_id,coalesce(a.target_driver_id,t.driver_id) AS driver_id,
 coalesce(a.target_shift_id,t.shift_id) AS shift_id,t.status,t.assigned_by,t.created_at,
 t.accepted_at,t.picked_up_at,t.delivered_at,t.proof,t.reason,t.transfer_shift_id,t.transfer_requested_by
 FROM driver_tasks t LEFT JOIN order_driver_attribution_corrections a ON a.source_task_id=t.id;

CREATE VIEW effective_collections AS
 SELECT p.id,p.order_id,coalesce(a.target_shift_id,p.shift_id) AS shift_id,
 coalesce(a.target_driver_id,p.collector_id) AS collector_id,p.method,p.amount_halalas,
 p.tendered_halalas,p.change_halalas,p.reference,p.created_at,p.amendment_revision
 FROM collections p LEFT JOIN order_driver_attribution_corrections a ON a.order_id=p.order_id;

-- A rollback image unaware of the overlay must never approve an old-attribution
-- closure. Compare identities, not monetary sums alone (card-only corrections
-- do not change the cash ledger). Existing snapshots are never rewritten.
CREATE FUNCTION protect_corrected_driver_closure() RETURNS trigger AS $$
DECLARE expected_tasks jsonb; actual_tasks jsonb; expected_collections jsonb; actual_collections jsonb;
BEGIN
 IF NEW.kind='driver' AND EXISTS(SELECT 1 FROM order_driver_attribution_corrections
       WHERE source_shift_id=NEW.shift_id OR target_shift_id=NEW.shift_id) THEN
   SELECT coalesce(jsonb_agg(jsonb_build_object('id',t.id,'driver_id',t.driver_id,'shift_id',t.shift_id) ORDER BY t.id),'[]'::jsonb)
     INTO expected_tasks FROM effective_driver_tasks t WHERE t.shift_id=NEW.shift_id;
   SELECT coalesce(jsonb_agg(jsonb_build_object('id',t->>'id','driver_id',t->>'driver_id','shift_id',t->>'shift_id') ORDER BY t->>'id'),'[]'::jsonb)
     INTO actual_tasks FROM jsonb_array_elements(coalesce(NEW.snapshot->'tasks','[]'::jsonb)) t;
   SELECT coalesce(jsonb_agg(jsonb_build_object('id',p.id,'collector_id',p.collector_id,'shift_id',p.shift_id) ORDER BY p.id),'[]'::jsonb)
     INTO expected_collections FROM effective_collections p WHERE p.shift_id=NEW.shift_id;
   SELECT coalesce(jsonb_agg(jsonb_build_object('id',p->>'id','collector_id',p->>'collector_id','shift_id',p->>'shift_id') ORDER BY p->>'id'),'[]'::jsonb)
     INTO actual_collections FROM jsonb_array_elements(coalesce(NEW.snapshot->'collections','[]'::jsonb)) p;
   IF actual_tasks IS DISTINCT FROM expected_tasks OR actual_collections IS DISTINCT FROM expected_collections THEN
     RAISE EXCEPTION 'Corrected driver attribution requires a compatible closure snapshot';
   END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER corrected_driver_closure_compatibility BEFORE INSERT ON closure_reports
 FOR EACH ROW EXECUTE FUNCTION protect_corrected_driver_closure();
