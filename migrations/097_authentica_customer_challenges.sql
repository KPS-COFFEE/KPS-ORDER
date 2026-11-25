-- Prepared provider only. Never selects/enables a channel, alters a credential,
-- rewrites a historical challenge or grants a work/owner authentication proof.
ALTER TABLE otp_challenges DROP CONSTRAINT otp_challenges_provider_name_check;
ALTER TABLE otp_challenges ADD CONSTRAINT otp_challenges_provider_name_check
 CHECK (provider_name IN ('twilio_verify','tawked','authentica'));
ALTER TABLE identity_channels DROP CONSTRAINT identity_channels_sms_provider_check;
ALTER TABLE identity_channels ADD CONSTRAINT identity_channels_sms_provider_check
 CHECK (sms_provider IN ('disabled','twilio_verify','tawked','authentica'));
ALTER TABLE identity_channels ADD CONSTRAINT identity_channels_authentica_scope
 CHECK (sms_provider <> 'authentica' OR (brand_id='enbishan' AND channel='sms'));
ALTER TABLE otp_challenges ADD COLUMN authentica_contract_hash TEXT;
ALTER TABLE otp_challenges ADD COLUMN authentica_code_length SMALLINT;
ALTER TABLE otp_challenges ADD CONSTRAINT otp_challenges_authentica_scope CHECK (
 (provider_name <> 'authentica' AND authentica_contract_hash IS NULL AND authentica_code_length IS NULL)
 OR (provider_name='authentica' AND brand_id='enbishan' AND delivery_channel='sms'
     AND source_session_id IS NULL AND (id LIKE 'otp\_%' ESCAPE '\' OR id LIKE 'owner\_probe\_%' ESCAPE '\')
     AND authentica_contract_hash ~ '^[a-f0-9]{64}$' AND authentica_contract_hash IS NOT NULL
     AND authentica_code_length BETWEEN 4 AND 10 AND authentica_code_length IS NOT NULL)
);
CREATE INDEX otp_authentica_phone_generations ON otp_challenges(brand_id,phone,created_at DESC)
 WHERE provider_name='authentica';
-- A successful delivery proof refresh is not a configuration change. A
-- dedicated generation binds in-flight sends without aborting unrelated phones.
ALTER TABLE identity_channels ADD COLUMN authentica_configuration_generation BIGINT NOT NULL DEFAULT 0
 CHECK (authentica_configuration_generation>=0);
CREATE FUNCTION enbishan_sms_configuration_generation() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 NEW.authentica_configuration_generation := OLD.authentica_configuration_generation;
 IF ROW(NEW.sms_provider,NEW.state,NEW.enabled,NEW.delivery_proven_at IS NULL)
    IS DISTINCT FROM ROW(OLD.sms_provider,OLD.state,OLD.enabled,OLD.delivery_proven_at IS NULL) THEN
  NEW.authentica_configuration_generation := OLD.authentica_configuration_generation+1;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER enbishan_sms_configuration_generation BEFORE UPDATE ON identity_channels
 FOR EACH ROW WHEN (OLD.brand_id='enbishan' AND OLD.channel='sms')
 EXECUTE FUNCTION enbishan_sms_configuration_generation();
