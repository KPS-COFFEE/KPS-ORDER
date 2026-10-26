CREATE TABLE complimentary_authorizations (
 id TEXT PRIMARY KEY,
 brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_id TEXT NOT NULL REFERENCES branches(id),
 authorized_by TEXT NOT NULL REFERENCES users(id),
 payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 executed_order_id TEXT UNIQUE REFERENCES orders(id) DEFERRABLE INITIALLY DEFERRED,
 executed_by TEXT REFERENCES users(id),
 executed_at TIMESTAMPTZ,
 CHECK ((executed_order_id IS NULL)=(executed_by IS NULL)),
 CHECK ((executed_order_id IS NULL)=(executed_at IS NULL))
);
CREATE INDEX complimentary_authorization_pending ON complimentary_authorizations(brand_id,branch_id,created_at) WHERE executed_order_id IS NULL;
CREATE FUNCTION protect_complimentary_authorization() RETURNS trigger AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Courtesy approvals cannot be deleted'; END IF;
 IF OLD.executed_order_id IS NOT NULL OR
    (to_jsonb(NEW)-'executed_order_id'-'executed_by'-'executed_at') IS DISTINCT FROM
    (to_jsonb(OLD)-'executed_order_id'-'executed_by'-'executed_at') OR NEW.executed_order_id IS NULL THEN
   RAISE EXCEPTION 'Courtesy approval can only be executed once';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER complimentary_authorizations_immutable BEFORE UPDATE OR DELETE ON complimentary_authorizations
FOR EACH ROW EXECUTE FUNCTION protect_complimentary_authorization();
