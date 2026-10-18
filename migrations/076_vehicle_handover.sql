-- Preserve the original immutable ledger identity; additional vehicle intervals
-- are materialized from the canonical, audited shift handover history.
CREATE OR REPLACE FUNCTION sync_vehicle_operating_log() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE vid text; p text; day date; first_log jsonb; log_state text;
BEGIN
 IF NEW.kind<>'driver' THEN RETURN NEW; END IF;
 first_log=coalesce(NEW.vehicle_log->'previous_segments'->0,NEW.vehicle_log);
 log_state=CASE WHEN first_log ? 'end' AND NEW.vehicle_log ? 'previous_segments' THEN 'closed' ELSE NEW.status END;
 p=regexp_replace(upper(first_log->'vehicle'->>'plate'),'[[:space:]]','','g');
 IF p IS NULL OR p='' THEN RETURN NEW; END IF;
 INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details)
 VALUES('veh_'||md5(NEW.brand_id||':'||p),NEW.brand_id,p,first_log->'vehicle'->>'plate',first_log->'vehicle') ON CONFLICT DO NOTHING;
 SELECT id INTO vid FROM fleet_vehicles WHERE brand_id=NEW.brand_id AND plate_key=p FOR UPDATE;
 day=coalesce(NEW.delivery_operational_date,(NEW.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date);
 INSERT INTO vehicle_operating_logs(shift_id,vehicle_id,operating_day,metrics)
 VALUES(NEW.id,vid,day,vehicle_metrics(first_log,log_state,NEW.submission,NEW.exception_reason))
 ON CONFLICT(shift_id) DO UPDATE SET metrics=EXCLUDED.metrics,revision=vehicle_operating_logs.revision+1;
 RETURN NEW;
END $$;

CREATE VIEW fleet_operating_segments AS
 SELECT l.*,0::bigint segment_no,
 coalesce((s.vehicle_log->'previous_segments'->0->'start'->>'recorded_at')::timestamptz,(s.vehicle_log->'start'->>'recorded_at')::timestamptz,s.opened_at) received_at,
 coalesce((s.vehicle_log->'previous_segments'->0->'end'->>'recorded_at')::timestamptz,(s.vehicle_log->'end'->>'recorded_at')::timestamptz,s.submitted_at,s.closed_at) returned_at,
 (s.vehicle_log ? 'previous_segments') multi_vehicle
 FROM vehicle_operating_logs l JOIN shifts s ON s.id=l.shift_id
 UNION ALL
 SELECT s.id,v.id,coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date),
 vehicle_metrics(part.log,CASE WHEN part.log ? 'end' THEN 'closed' ELSE s.status END,s.submission,s.exception_reason),
 1,s.opened_at,part.n-1,
 (part.log->'start'->>'recorded_at')::timestamptz,
 coalesce((part.log->'end'->>'recorded_at')::timestamptz,CASE WHEN part.n=jsonb_array_length(s.vehicle_log->'previous_segments')+1 THEN coalesce(s.submitted_at,s.closed_at) END),true
 FROM shifts s CROSS JOIN LATERAL jsonb_array_elements(coalesce(s.vehicle_log->'previous_segments','[]') || jsonb_build_array(s.vehicle_log-'previous_segments')) WITH ORDINALITY part(log,n)
 JOIN fleet_vehicles v ON v.brand_id=s.brand_id AND v.plate_key=regexp_replace(upper(part.log->'vehicle'->>'plate'),'[[:space:]]','','g')
 WHERE s.kind='driver' AND s.vehicle_log ? 'previous_segments' AND part.n>1;
