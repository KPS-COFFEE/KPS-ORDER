-- Additive: historical photos remain explicitly missing GPS, never backfilled.
ALTER TABLE delivery_photos ADD COLUMN capture_location jsonb;
