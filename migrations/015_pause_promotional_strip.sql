-- Owner instruction: temporarily pause only the KPS text strip.
-- Forward migration runs once. Phrases, speed, quotes, themes and image
-- campaigns remain saved; a later explicit owner activation survives boots.
WITH paused AS (
    UPDATE app_settings
    SET settings=jsonb_set(settings,'{homepage_presentation}',
        coalesce(settings->'homepage_presentation','{}'::jsonb) || '{"ticker_enabled":false}'::jsonb),
        updated_at=now()
    WHERE brand_id='kps'
    RETURNING brand_id
)
INSERT INTO audit_events(id,action,entity_type,entity_id,details)
SELECT 'owner-ticker-paused-2026-10-10','presentation.owner_ticker_paused',
       'app_settings',brand_id,'{"owner_instruction":true,"phrases_preserved":true,"images_preserved":true}'::jsonb
FROM paused
ON CONFLICT DO NOTHING;
