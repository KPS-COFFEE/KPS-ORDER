-- Temporary, explicitly targeted requests. Expiry is enforced by the server.
CREATE TABLE driver_check_requests (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id),
 driver_id text NOT NULL REFERENCES users(id), shift_id text NOT NULL REFERENCES shifts(id),
 vehicle_id text NOT NULL REFERENCES fleet_vehicles(id), segment_started_at text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('odometer','fuel')),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','submitted','approved','rejected','cancelled')),
 requested_by text NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '20 minutes',
 response jsonb, responded_at timestamptz, decided_by text REFERENCES users(id), decided_at timestamptz,
 decision jsonb
);
CREATE INDEX driver_check_inbox ON driver_check_requests(driver_id,status,expires_at);
CREATE INDEX driver_check_brand ON driver_check_requests(brand_id,created_at DESC);

-- Keep original observations; don't calculate mileage across a corrected scale.
CREATE OR REPLACE FUNCTION vehicle_metrics(log jsonb, state text, sub jsonb, reason text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE a numeric; b numeric; liters numeric; km numeric; efficiency numeric; full_start boolean; full_end boolean; baseline numeric:=10;
BEGIN
 IF jsonb_typeof(log->'vehicle'->'baseline_kml')='number' THEN
  baseline=(log->'vehicle'->>'baseline_kml')::numeric;
  IF baseline<1 OR baseline>60 THEN baseline=10; END IF;
 END IF;
 a=(log->'start'->>'odometer_km')::numeric; b=(log->'end'->>'odometer_km')::numeric;
 SELECT coalesce(sum((x->>'liters')::numeric),0) INTO liters FROM jsonb_array_elements(coalesce(log->'refuels','[]')) x;
 full_start=CASE WHEN log->'start' IS NULL THEN NULL ELSE (log->'start'->>'fuel_percent')::int=100 END;
 full_end=CASE WHEN log->'end' IS NULL THEN NULL ELSE (log->'end'->>'fuel_percent')::int=100 END;
 km=CASE WHEN b>=a AND NOT coalesce((log->>'odometer_adjusted')::boolean,false) THEN b-a ELSE NULL END;
 IF full_start AND full_end AND liters>0 AND km>0 AND state IN ('submitted','closed') THEN efficiency=km/liters; END IF;
 RETURN jsonb_build_object('start_odometer',a,'end_odometer',b,'actual_distance',km,
 'gps_distance',NULL,'expected_distance',NULL,'distance_variance',NULL,
 'liters',liters,'efficiency',round(efficiency,3),'full_start',full_start,'full_end',full_end,
 'fuel_status',CASE WHEN efficiency IS NULL THEN 'grey' WHEN efficiency>=baseline*.9 THEN 'green' WHEN efficiency>=baseline*.8 THEN 'orange' ELSE 'red' END,
 'baseline_kml',baseline,'policy_version',3,
 'exception',coalesce(log ? 'odometer_adjusted' OR (log->'fuel_readiness'->>'temporary_exception')::boolean OR log ? 'end_exception' OR sub ? 'delegated_closure' OR sub ? 'closure_exception' OR nullif(reason,'') IS NOT NULL,false),
 'notes',concat_ws(E'\n',CASE WHEN log ? 'odometer_adjusted' THEN 'تم تصحيح العداد؛ المسافة والكفاءة تحتاج مراجعة / Odometer corrected; distance and efficiency require review' END,nullif(log->'start'->>'notes',''),nullif(log->'end'->>'notes',''),nullif(log->'end_exception'->>'reason',''),nullif(reason,'')),
 'record_status',CASE WHEN log ? 'end_exception' OR sub ? 'delegated_closure' OR sub ? 'closure_exception' OR nullif(reason,'') IS NOT NULL THEN 'exception' WHEN state IN ('submitted','closed') THEN 'closed' ELSE 'open' END);
END $$;
