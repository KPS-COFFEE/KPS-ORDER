ALTER TABLE fleet_missions ADD COLUMN vehicle_changes jsonb NOT NULL DEFAULT '[]';
