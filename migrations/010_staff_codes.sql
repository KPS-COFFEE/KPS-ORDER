-- Additive: canonical principals, password hashes, sessions and prior links survive.
ALTER TABLE identity_tokens ADD COLUMN IF NOT EXISTS code_hash TEXT;
ALTER TABLE identity_tokens ADD COLUMN IF NOT EXISTS staff_context JSONB;

-- Index normalizes formatting without rewriting stored contacts or inventing ownership.
CREATE OR REPLACE FUNCTION normalized_identity_phone(value TEXT) RETURNS TEXT
LANGUAGE SQL IMMUTABLE STRICT AS $$
 WITH compact AS (
  SELECT regexp_replace(translate(value,'٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789'), '[[:space:]()-]', '', 'g') AS phone
 ), canonical AS (
  SELECT CASE WHEN phone ~ '^05[0-9]{8}$' THEN '+966'||substring(phone FROM 2)
              WHEN left(phone,2)='00' THEN '+'||substring(phone FROM 3)
              ELSE phone END AS phone FROM compact
 ) SELECT CASE WHEN phone ~ '^\+[1-9][0-9]{7,14}$' THEN phone ELSE NULL END FROM canonical
$$;
-- Existing ambiguous records fail closed at login; no destructive deduplication.
CREATE INDEX IF NOT EXISTS users_staff_phone_lookup ON users(brand_id,normalized_identity_phone(phone))
 WHERE role<>'customer' AND phone IS NOT NULL;
