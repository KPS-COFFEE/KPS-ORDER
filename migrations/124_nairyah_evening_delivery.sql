-- Owner approval: KPS branch 6, full Nairyah city, evening delivery.
-- Envelope includes every city-115 district in homaily/Saudi-Arabia-Regions-Cities-and-Districts.
-- Existing location, pickup/car services and commercial policy are preserved.
DO $$
DECLARE target branches%ROWTYPE; source branches%ROWTYPE; changes jsonb;
BEGIN
 SELECT * INTO target FROM branches WHERE id='kps-branch-6' AND brand_id='kps' AND branch_no=6 AND city='النعيرية' FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT * INTO source FROM branches WHERE brand_id='kps' AND branch_no=11;
 IF source.settings->>'delivery_fee_halalas' IS NULL OR source.settings->>'minimum_order_halalas' IS NULL THEN
 RAISE EXCEPTION 'Existing outside-Hafar commercial policy is required'; END IF;
 IF target.settings->'location' IS NULL OR target.settings->'location'='null'::jsonb THEN
 RAISE EXCEPTION 'Verified Nairyah branch pin required'; END IF;
 changes='{"active": true, "delivery_enabled": true, "ordering_verified": true, "delivery_hours": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "16:00", "end": "04:00"}], "payment_methods": ["cash", "card"], "coverage": {"type": "polygon", "coordinates": [[48.519403, 27.450366], [48.519373, 27.450371], [48.519344, 27.450381], [48.461932, 27.470383], [48.462987, 27.472649], [48.467132, 27.481449], [48.468593, 27.484352], [48.468606, 27.484372], [48.468622, 27.484391], [48.468641, 27.484406], [48.468662, 27.484418], [48.468685, 27.484426], [48.468709, 27.484431], [48.516584, 27.49023], [48.536887, 27.468227], [48.537035, 27.468066], [48.519544, 27.450412], [48.51952, 27.450394], [48.519493, 27.45038], [48.519464, 27.45037], [48.519434, 27.450366], [48.519403, 27.450366]]}}'::jsonb;
 IF target.settings->'opening_hours' IS NULL OR target.settings->'opening_hours'='null'::jsonb THEN
 changes=changes||jsonb_build_object('opening_hours',changes->'delivery_hours'); END IF;
 IF target.settings->>'delivery_fee_halalas' IS NULL THEN
 changes=changes||jsonb_build_object('delivery_fee_halalas',source.settings->'delivery_fee_halalas'); END IF;
 IF target.settings->>'minimum_order_halalas' IS NULL THEN
 changes=changes||jsonb_build_object('minimum_order_halalas',source.settings->'minimum_order_halalas'); END IF;
 INSERT INTO audit_events(id,action,entity_type,entity_id,details) VALUES('owner-nairyah-evening-20261027','branch.owner_delivery_activation','branch',target.id,jsonb_build_object('previous_settings',target.settings,'changes',changes,'commercial_source_branch',source.id,'owner_authorized',true));
 UPDATE branches SET settings=settings||changes,updated_at=now() WHERE id=target.id;
END $$;
