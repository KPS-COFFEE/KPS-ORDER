-- Owner decision: morning then evening share one Riyadh reporting day.
-- Additive projection: all original shift columns and closure bytes survive.
ALTER TABLE shifts ADD COLUMN reporting_operational_date date GENERATED ALWAYS AS (
 CASE WHEN delivery_window_snapshot->>'period'='morning'
      THEN (opened_at AT TIME ZONE 'Asia/Riyadh')::date
      WHEN delivery_window_snapshot->>'period'='evening' AND delivery_operational_date IS NOT NULL
      THEN delivery_operational_date
      ELSE ((opened_at AT TIME ZONE 'Asia/Riyadh') - interval '4 hours')::date END
) STORED;
CREATE INDEX shifts_reporting_day ON shifts(brand_id,reporting_operational_date);

CREATE TABLE operational_day_reclassifications (
 shift_id text PRIMARY KEY REFERENCES shifts(id),
 brand_id text NOT NULL REFERENCES brands(id),
 original_day date,
 corrected_day date NOT NULL,
 decision text NOT NULL DEFAULT 'owner-morning-then-evening-20261025',
 reason text NOT NULL DEFAULT 'جمع الشفت الصباحي ثم المسائي في يوم واحد؛ تصحيح تصنيف التاريخ فقط',
 created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO operational_day_reclassifications(shift_id,brand_id,original_day,corrected_day)
 SELECT id,brand_id,delivery_operational_date,reporting_operational_date FROM shifts
 WHERE delivery_operational_date IS DISTINCT FROM reporting_operational_date;
CREATE TRIGGER immutable_operational_day_reclassification
 BEFORE UPDATE OR DELETE ON operational_day_reclassifications
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
