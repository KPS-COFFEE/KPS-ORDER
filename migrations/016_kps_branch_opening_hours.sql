-- Owner-approved KPS branch opening hours, Riyadh (2026-10-10).
-- Monday=0 / Friday=4. Twenty source-verified branches only.
-- Preserve custom hours AND explicit [] pauses. This forward migration
-- runs once; it changes no service flags, payments, locations, or orders.
-- Fresh installs use the matching default in data/branches.json.
WITH changed AS (
    UPDATE branches AS b
    SET settings=jsonb_set(
          b.settings,'{opening_hours}',
          ('[{"days":[0,1,2,3,5,6],"start":"00:00","end":"00:00"},'
          ||'{"days":[4],"start":"00:00","end":"05:30"},'
          ||'{"days":[4],"start":"12:30","end":"00:00"}]')::jsonb,
          true
        ),
        updated_at=now()
    WHERE b.brand_id='kps'
      AND b.branch_no BETWEEN 1 AND 20
      AND b.id='kps-branch-' || b.branch_no::text
      AND b.source_id='apex-canonical-branch-no:' || b.branch_no::text
      AND (b.settings->'opening_hours' IS NULL
           OR b.settings->'opening_hours'='null'::jsonb)
    RETURNING b.id,b.branch_no
)
INSERT INTO audit_events(id,action,entity_type,entity_id,details)
SELECT 'kps-opening-hours-20261010-' || id,
       'branch.owner_opening_hours','branch',id,
       jsonb_build_object('owner_approved',true,'branch_no',branch_no,
                          'timezone','Asia/Riyadh',
                          'friday_closed','05:30-12:30',
                          'other_days','24h',
                          'service_enablement_unchanged',true)
FROM changed
ON CONFLICT DO NOTHING;
