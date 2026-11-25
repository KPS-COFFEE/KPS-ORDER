-- A vehicle has one permanent identity across drivers. Shifts remain canonical.
CREATE TABLE fleet_vehicles (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id),
 plate_key text NOT NULL, plate text NOT NULL, details jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(brand_id,plate_key)
);
CREATE TABLE vehicle_operating_logs (
 shift_id text PRIMARY KEY REFERENCES shifts(id),
 vehicle_id text NOT NULL REFERENCES fleet_vehicles(id),
 operating_day date NOT NULL, metrics jsonb NOT NULL DEFAULT '{}',
 revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX vehicle_log_history ON vehicle_operating_logs(vehicle_id,operating_day DESC,shift_id);
CREATE INDEX vehicle_log_day ON vehicle_operating_logs(operating_day DESC,vehicle_id);
CREATE TABLE vehicle_log_corrections (
 id text PRIMARY KEY, shift_id text NOT NULL REFERENCES vehicle_operating_logs(shift_id),
 revision integer NOT NULL, actor_id text NOT NULL REFERENCES users(id),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
 before_metrics jsonb NOT NULL, after_metrics jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(shift_id,revision)
);
CREATE FUNCTION protect_vehicle_correction() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Vehicle correction is append-only'; END $$;
CREATE TRIGGER immutable_vehicle_correction BEFORE UPDATE OR DELETE ON vehicle_log_corrections
 FOR EACH ROW EXECUTE FUNCTION protect_vehicle_correction();

CREATE FUNCTION vehicle_metrics(log jsonb, state text, sub jsonb, reason text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE a numeric; b numeric; liters numeric; km numeric; efficiency numeric; full_start boolean; full_end boolean;
BEGIN
 a=(log->'start'->>'odometer_km')::numeric; b=(log->'end'->>'odometer_km')::numeric;
 SELECT coalesce(sum((x->>'liters')::numeric),0) INTO liters FROM jsonb_array_elements(coalesce(log->'refuels','[]')) x;
 full_start=CASE WHEN log->'start' IS NULL THEN NULL ELSE (log->'start'->>'fuel_percent')::int=100 END;
 full_end=CASE WHEN log->'end' IS NULL THEN NULL ELSE (log->'end'->>'fuel_percent')::int=100 END;
 km=CASE WHEN b>=a THEN b-a ELSE NULL END;
 IF full_start AND full_end AND liters>0 AND km>0 AND state IN ('submitted','closed') THEN efficiency=km/liters; END IF;
 RETURN jsonb_build_object('start_odometer',a,'end_odometer',b,'actual_distance',km,
 'gps_distance',NULL,'expected_distance',NULL,'distance_variance',NULL,
 'liters',liters,'efficiency',round(efficiency,3),'full_start',full_start,'full_end',full_end,
 'fuel_status',CASE WHEN efficiency IS NULL THEN 'grey' WHEN efficiency>=9 THEN 'green' WHEN efficiency>=8 THEN 'orange' ELSE 'red' END,
 'baseline_kml',10,'policy_version',1,
 'exception',coalesce(log ? 'end_exception' OR sub ? 'delegated_closure' OR sub ? 'closure_exception' OR nullif(reason,'') IS NOT NULL,false),
 'notes',concat_ws(E'\n',nullif(log->'start'->>'notes',''),nullif(log->'end'->>'notes',''),nullif(log->'end_exception'->>'reason',''),nullif(reason,'')),
 'record_status',CASE WHEN log ? 'end_exception' OR sub ? 'delegated_closure' OR sub ? 'closure_exception' OR nullif(reason,'') IS NOT NULL THEN 'exception' WHEN state IN ('submitted','closed') THEN 'closed' ELSE 'open' END);
END $$;

-- Historical registration is additive: no existing shift/driver/fuel record is edited.
INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details)
 SELECT DISTINCT ON (brand_id,regexp_replace(upper(vehicle_log->'vehicle'->>'plate'),'[[:space:]]','','g'))
 'veh_'||md5(brand_id||':'||regexp_replace(upper(vehicle_log->'vehicle'->>'plate'),'[[:space:]]','','g')),
 brand_id,regexp_replace(upper(vehicle_log->'vehicle'->>'plate'),'[[:space:]]','','g'),
 vehicle_log->'vehicle'->>'plate',vehicle_log->'vehicle'
 FROM shifts WHERE kind='driver' AND nullif(regexp_replace(upper(vehicle_log->'vehicle'->>'plate'),'[[:space:]]','','g'),'') IS NOT NULL
 ORDER BY brand_id,regexp_replace(upper(vehicle_log->'vehicle'->>'plate'),'[[:space:]]','','g'),opened_at,id;
INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details)
 SELECT DISTINCT ON (brand_id,regexp_replace(upper(vehicle->>'plate'),'[[:space:]]','','g'))
 'veh_'||md5(brand_id||':'||regexp_replace(upper(vehicle->>'plate'),'[[:space:]]','','g')),
 brand_id,regexp_replace(upper(vehicle->>'plate'),'[[:space:]]','','g'),vehicle->>'plate',vehicle
 FROM users WHERE role='driver' AND nullif(regexp_replace(upper(vehicle->>'plate'),'[[:space:]]','','g'),'') IS NOT NULL
 ORDER BY brand_id,regexp_replace(upper(vehicle->>'plate'),'[[:space:]]','','g'),id ON CONFLICT DO NOTHING;
INSERT INTO vehicle_operating_logs(shift_id,vehicle_id,operating_day,metrics)
 SELECT s.id,v.id,coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date),
 vehicle_metrics(s.vehicle_log,s.status,s.submission,s.exception_reason)
 FROM shifts s JOIN fleet_vehicles v ON v.brand_id=s.brand_id AND v.plate_key=regexp_replace(upper(s.vehicle_log->'vehicle'->>'plate'),'[[:space:]]','','g') WHERE s.kind='driver';

CREATE FUNCTION sync_vehicle_operating_log() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE vid text; p text; day date;
BEGIN
 IF NEW.kind<>'driver' THEN RETURN NEW; END IF;
 p=regexp_replace(upper(NEW.vehicle_log->'vehicle'->>'plate'),'[[:space:]]','','g');
 IF p IS NULL OR p='' THEN RETURN NEW; END IF;
 INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details)
 VALUES('veh_'||md5(NEW.brand_id||':'||p),NEW.brand_id,p,NEW.vehicle_log->'vehicle'->>'plate',NEW.vehicle_log->'vehicle') ON CONFLICT DO NOTHING;
 SELECT id INTO vid FROM fleet_vehicles WHERE brand_id=NEW.brand_id AND plate_key=p FOR UPDATE;
 day=coalesce(NEW.delivery_operational_date,(NEW.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date);
 INSERT INTO vehicle_operating_logs(shift_id,vehicle_id,operating_day,metrics)
 VALUES(NEW.id,vid,day,vehicle_metrics(NEW.vehicle_log,NEW.status,NEW.submission,NEW.exception_reason))
 ON CONFLICT(shift_id) DO UPDATE SET metrics=EXCLUDED.metrics,revision=vehicle_operating_logs.revision+1;
 RETURN NEW;
END $$;
CREATE TRIGGER sync_vehicle_log AFTER INSERT OR UPDATE OF vehicle_log,status,submission,exception_reason ON shifts
 FOR EACH ROW EXECUTE FUNCTION sync_vehicle_operating_log();

CREATE FUNCTION register_fleet_vehicle() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p text;
BEGIN
 p=regexp_replace(upper(NEW.vehicle->>'plate'),'[[:space:]]','','g');
 IF NEW.role='driver' AND p IS NOT NULL AND p<>'' THEN
  INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details)
  VALUES('veh_'||md5(NEW.brand_id||':'||p),NEW.brand_id,p,NEW.vehicle->>'plate',NEW.vehicle) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER register_fleet_vehicle AFTER INSERT OR UPDATE OF vehicle ON users
 FOR EACH ROW EXECUTE FUNCTION register_fleet_vehicle();

-- Identity/day are stable; closed metric snapshots can change only through the
-- canonical shift materialization trigger. Corrections append a separate row.
CREATE FUNCTION protect_vehicle_operating_log() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Vehicle history cannot be deleted'; END IF;
 IF NEW.shift_id<>OLD.shift_id OR NEW.vehicle_id<>OLD.vehicle_id OR NEW.operating_day<>OLD.operating_day OR NEW.created_at<>OLD.created_at THEN
  RAISE EXCEPTION 'Vehicle log identity is immutable';
 END IF;
 IF NEW.metrics IS DISTINCT FROM OLD.metrics AND pg_trigger_depth()=1 AND EXISTS(SELECT 1 FROM shifts WHERE id=OLD.shift_id AND status='closed') THEN
  RAISE EXCEPTION 'Closed vehicle metrics require an append-only correction';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER protect_vehicle_operating_log BEFORE UPDATE OR DELETE ON vehicle_operating_logs
 FOR EACH ROW EXECUTE FUNCTION protect_vehicle_operating_log();
