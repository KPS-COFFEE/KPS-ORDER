-- Additive support context; existing order-linked conversations retain their IDs/history.
ALTER TABLE conversations ALTER COLUMN order_id DROP NOT NULL;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS context_service TEXT;
UPDATE conversations v SET context_service=o.service FROM orders o
 WHERE v.order_id=o.id AND v.context_service IS NULL;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='conversations_context_service_valid' AND conrelid='conversations'::regclass) THEN
  ALTER TABLE conversations ADD CONSTRAINT conversations_context_service_valid
   CHECK(context_service IS NULL OR context_service IN ('delivery','pickup','car'));
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='conversations_context_required' AND conrelid='conversations'::regclass) THEN
  ALTER TABLE conversations ADD CONSTRAINT conversations_context_required
   CHECK(order_id IS NOT NULL OR context_service IS NOT NULL);
 END IF;
END $$;
CREATE INDEX IF NOT EXISTS conversations_unlinked_context ON conversations(customer_id,branch_id,context_service)
 WHERE order_id IS NULL;
