-- Revocation is separate evidence; the original exact approval stays immutable.
CREATE TABLE complimentary_authorization_revocations (
 authorization_id TEXT PRIMARY KEY REFERENCES complimentary_authorizations(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 revoked_by TEXT NOT NULL REFERENCES users(id),
 reason TEXT NOT NULL CHECK(length(trim(reason))>=5),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION check_complimentary_revocation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE approval complimentary_authorizations%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Courtesy revocations are immutable'; END IF;
 SELECT * INTO approval FROM complimentary_authorizations WHERE id=NEW.authorization_id AND brand_id=NEW.brand_id FOR UPDATE;
 IF NOT FOUND OR approval.executed_order_id IS NOT NULL THEN
   RAISE EXCEPTION 'Only an unused courtesy approval can be revoked';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER complimentary_revocation_guard BEFORE INSERT OR UPDATE OR DELETE ON complimentary_authorization_revocations
FOR EACH ROW EXECUTE FUNCTION check_complimentary_revocation();
CREATE FUNCTION check_complimentary_execution_not_revoked() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.executed_order_id IS NOT NULL AND EXISTS (SELECT 1 FROM complimentary_authorization_revocations WHERE authorization_id=NEW.id) THEN
   RAISE EXCEPTION 'Revoked courtesy approval cannot be executed';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER complimentary_execution_not_revoked BEFORE UPDATE ON complimentary_authorizations
FOR EACH ROW EXECUTE FUNCTION check_complimentary_execution_not_revoked();
