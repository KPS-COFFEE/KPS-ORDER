-- Manual, attributed Google Maps itinerary readings; never overwrite shift ledgers.
CREATE TABLE fleet_route_readings (
 id text PRIMARY KEY, shift_id text NOT NULL REFERENCES vehicle_operating_logs(shift_id),
 revision integer NOT NULL, actor_id text NOT NULL REFERENCES users(id),
 expected_km numeric(12,3) NOT NULL CHECK(expected_km>0),
 source_url text NOT NULL, itinerary text NOT NULL, reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(shift_id,revision)
);
CREATE TRIGGER immutable_fleet_route_reading BEFORE UPDATE OR DELETE ON fleet_route_readings
 FOR EACH ROW EXECUTE FUNCTION protect_vehicle_correction();

CREATE FUNCTION fleet_distance_review(m jsonb, expected numeric, started timestamptz, ended timestamptz)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE actual numeric; hours numeric; delta numeric; pct numeric; color text:='grey'; issue text:='missing_route';
BEGIN
 actual=(m->>'actual_distance')::numeric;
 hours=extract(epoch FROM(ended-started))/3600;
 -- Conservative sanity guard, not a speed limit or an allegation against a driver.
 IF actual IS NOT NULL AND ended IS NOT NULL AND
    (actual<0 OR hours<=0 OR actual>greatest(20,hours*180)) THEN
   color='red';issue='invalid_odometer';
   m=m||jsonb_build_object('efficiency',NULL,'fuel_status','grey','distance_eligible',false);
 ELSIF m->>'record_status'='open' OR actual IS NULL OR ended IS NULL THEN issue='incomplete';
 ELSIF expected>0 THEN
   delta=actual-expected;pct=abs(delta)/expected*100;
   color=CASE WHEN abs(delta)<=5 OR pct<=10 THEN 'green' WHEN pct<=25 THEN 'orange' ELSE 'red' END;
   issue=CASE WHEN color='green' THEN 'within_tolerance' ELSE 'review_difference' END;
 END IF;
 RETURN m||jsonb_build_object('expected_distance',expected,'distance_variance',CASE WHEN expected>0 THEN actual-expected ELSE NULL END,
   'distance_variance_percent',CASE WHEN expected>0 THEN round((actual-expected)/expected*100,1) ELSE NULL END,
   'distance_status',color,'distance_review',issue,'distance_policy_version',1,
   'distance_eligible',issue<>'invalid_odometer');
END $$;
