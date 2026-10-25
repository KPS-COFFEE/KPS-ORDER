-- Owner-approved default: delivery unchanged; pickup and car remain opt-in.
-- Applied once, so later explicit admin changes are retained on future deploys.
UPDATE brands SET settings = jsonb_set(
  COALESCE(settings, '{}'::jsonb), '{services}',
  COALESCE(settings->'services', '{}'::jsonb) || '{"pickup":false,"car_pickup":false}'::jsonb
) WHERE id = 'enbishan';
