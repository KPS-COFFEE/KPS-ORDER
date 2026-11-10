-- Ending NEW staffing admission does not close custody, tasks or accounting.
ALTER TABLE shifts ADD COLUMN manager_admission_ended_at timestamptz;
CREATE FUNCTION guard_manager_admission_end() RETURNS trigger AS $$
BEGIN
 IF NEW.manager_admission_ended_at IS NOT NULL AND NEW.kind<>'manager' THEN
  RAISE EXCEPTION 'Only manager staffing admission can end independently';
 END IF;
 IF TG_OP='UPDATE' AND OLD.manager_admission_ended_at IS NOT NULL
    AND NEW.manager_admission_ended_at IS DISTINCT FROM OLD.manager_admission_ended_at THEN
  RAISE EXCEPTION 'Manager admission end is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER manager_admission_end_guard BEFORE INSERT OR UPDATE ON shifts
 FOR EACH ROW EXECUTE FUNCTION guard_manager_admission_end();
-- Preserve the original index name: the guarded multibrand bootstrap replaces it.
DO $$ BEGIN
 IF to_regclass('shifts_one_active_user_brand') IS NULL THEN
  DROP INDEX IF EXISTS shifts_one_active_user;
  CREATE UNIQUE INDEX shifts_one_active_user ON shifts(user_id)
   WHERE status<>'closed' AND manager_admission_ended_at IS NULL;
 END IF;
END $$;

-- Linked attribution corrections preserve every original bag/declaration/report.
CREATE TABLE manager_day_splits (
 id text PRIMARY KEY,
 source_shift_id text NOT NULL REFERENCES shifts(id),
 target_shift_id text NOT NULL UNIQUE REFERENCES shifts(id),
 brand_id text NOT NULL REFERENCES brands(id),
 corrected_day date NOT NULL,
 original_state jsonb NOT NULL,
 validation jsonb NOT NULL,
 recorded_by text NOT NULL REFERENCES users(id),
 reason text NOT NULL CHECK(length(btrim(reason))>=5),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(source_shift_id,corrected_day)
);
CREATE TRIGGER immutable_manager_day_split BEFORE UPDATE OR DELETE ON manager_day_splits
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();

-- A previous application may still use the frozen manager ID in a driver
-- declaration. Reject such writes rather than crediting the wrong day.
CREATE FUNCTION guard_split_custody_attribution() RETURNS trigger AS $$
BEGIN
 IF NEW.closure AND NEW.to_shift_id IS NOT NULL AND EXISTS (
  SELECT 1 FROM shifts child JOIN manager_day_splits split ON split.target_shift_id=child.manager_shift_id
  WHERE child.id=NEW.from_shift_id AND NEW.to_shift_id<>split.target_shift_id
    AND split.original_state->'drivers' @> jsonb_build_array(jsonb_build_object('id',child.id))
 ) THEN
  RAISE EXCEPTION 'Closure custody must follow the documented manager day split';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER split_custody_attribution_guard BEFORE INSERT OR UPDATE ON custody_transfers
 FOR EACH ROW EXECUTE FUNCTION guard_split_custody_attribution();
