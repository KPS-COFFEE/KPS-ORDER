-- An explicit new owner-only grant is required after manager closure. Existing
-- grants retain their open-manager restriction; no original ledger is reopened.
ALTER TABLE prior_delivery_grants ADD COLUMN closed_manager_supplement boolean NOT NULL DEFAULT false;
