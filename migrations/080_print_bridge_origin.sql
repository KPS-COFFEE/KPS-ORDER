-- Additive telemetry only: existing devices, tokens, approval and jobs unchanged.
ALTER TABLE branch_print_devices ADD COLUMN IF NOT EXISTS last_api_host text;
ALTER TABLE branch_print_devices ADD COLUMN IF NOT EXISTS bridge_version text;
