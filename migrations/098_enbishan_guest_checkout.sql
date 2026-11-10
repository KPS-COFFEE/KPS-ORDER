-- Owner-authorized guest ordering for Enbishan only. OTP remains available
-- for account recovery, saved addresses and verified-identity promotions.
UPDATE app_settings SET settings=jsonb_set(settings,'{customer_phone_required}','false'::jsonb,true)
 WHERE brand_id='enbishan';
INSERT INTO identity_channels(brand_id,channel,state,enabled)
 SELECT id,'guest','active',true FROM brands WHERE id='enbishan'
 ON CONFLICT(brand_id,channel) DO UPDATE SET state='active',enabled=true;
