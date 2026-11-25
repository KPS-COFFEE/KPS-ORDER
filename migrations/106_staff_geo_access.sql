-- A geographic exception never authenticates a person or grants permissions.
-- An owner issues a single-use entry secret, exchanged for a separate browser
-- secret. Only SHA-256 hashes are retained; expiry/revocation is checked live.
CREATE TABLE staff_geo_access_grants (
 id text PRIMARY KEY,
 user_id text NOT NULL REFERENCES users(id),
 brand_id text NOT NULL REFERENCES brands(id),
 approved_by text NOT NULL REFERENCES users(id),
 owner_brand_id text NOT NULL REFERENCES brands(id),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 500),
 entry_token_hash text NOT NULL UNIQUE CHECK(entry_token_hash ~ '^[0-9a-f]{64}$'),
 access_token_hash text UNIQUE CHECK(access_token_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 redeemed_at timestamptz,
 revoked_at timestamptz,
 revoked_by text REFERENCES users(id),
 revocation_reason text,
 revocation_source text CHECK(revocation_source IN ('owner','restore')),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '168 hours'),
 CHECK((redeemed_at IS NULL)=(access_token_hash IS NULL)),
 CHECK((revoked_at IS NULL)=(revocation_reason IS NULL)),
 CHECK((revoked_at IS NULL)=(revocation_source IS NULL)),
 CHECK((revoked_at IS NULL AND revoked_by IS NULL) OR
       (revoked_at IS NOT NULL AND ((revocation_source='owner' AND revoked_by IS NOT NULL) OR
                                    (revocation_source='restore' AND revoked_by IS NULL)))),
 CHECK(revocation_reason IS NULL OR length(btrim(revocation_reason)) BETWEEN 3 AND 500)
);
CREATE INDEX staff_geo_access_target ON staff_geo_access_grants(user_id,created_at DESC);
CREATE INDEX staff_geo_access_owner ON staff_geo_access_grants(approved_by,created_at DESC);
