-- Owner instruction: temporarily pause only the KPS text strip.
-- Forward migration runs once. Phrases, speed, quotes, themes and image
-- campaigns remain saved; a later explicit owner activation survives boots.
UPDATE app_settings
SET settings=jsonb_set(settings,'{homepage_presentation}',
    coalesce(settings->'homepage_presentation','{}'::jsonb) || '{"ticker_enabled":false}'::jsonb),
    updated_at=now()
WHERE brand_id='kps';

INSERT INTO audit_events(id,action,entity_type,entity_id,details)
VALUES('owner-ticker-paused-2026-10-10','presentation.owner_ticker_paused',
       'app_settings','kps','{"owner_instruction":true,"phrases_preserved":true,"images_preserved":true}')
ON CONFLICT DO NOTHING;
