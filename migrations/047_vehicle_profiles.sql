-- Per-car operating baseline. Existing shift snapshots and corrections are retained.
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
 km=CASE WHEN b>=a THEN b-a ELSE NULL END;
 IF full_start AND full_end AND liters>0 AND km>0 AND state IN ('submitted','closed') THEN efficiency=km/liters; END IF;
 RETURN jsonb_build_object('start_odometer',a,'end_odometer',b,'actual_distance',km,
 'gps_distance',NULL,'expected_distance',NULL,'distance_variance',NULL,
 'liters',liters,'efficiency',round(efficiency,3),'full_start',full_start,'full_end',full_end,
 'fuel_status',CASE WHEN efficiency IS NULL THEN 'grey' WHEN efficiency>=baseline*.9 THEN 'green' WHEN efficiency>=baseline*.8 THEN 'orange' ELSE 'red' END,
 'baseline_kml',baseline,'policy_version',2,
 'exception',coalesce(log ? 'end_exception' OR sub ? 'delegated_closure' OR sub ? 'closure_exception' OR nullif(reason,'') IS NOT NULL,false),
 'notes',concat_ws(E'\n',nullif(log->'start'->>'notes',''),nullif(log->'end'->>'notes',''),nullif(log->'end_exception'->>'reason',''),nullif(reason,'')),
 'record_status',CASE WHEN log ? 'end_exception' OR sub ? 'delegated_closure' OR sub ? 'closure_exception' OR nullif(reason,'') IS NOT NULL THEN 'exception' WHEN state IN ('submitted','closed') THEN 'closed' ELSE 'open' END);
END $$;

-- Updating a driver's car details refreshes the same permanent card, never its history.
CREATE OR REPLACE FUNCTION register_fleet_vehicle() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p text;
BEGIN
 p=regexp_replace(upper(NEW.vehicle->>'plate'),'[[:space:]]','','g');
 IF NEW.role='driver' AND p IS NOT NULL AND p<>'' THEN
  INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details)
  VALUES('veh_'||md5(NEW.brand_id||':'||p),NEW.brand_id,p,NEW.vehicle->>'plate',NEW.vehicle)
  ON CONFLICT(brand_id,plate_key) DO UPDATE SET details=fleet_vehicles.details||EXCLUDED.details,plate=EXCLUDED.plate;
 END IF;
 RETURN NEW;
END $$;
