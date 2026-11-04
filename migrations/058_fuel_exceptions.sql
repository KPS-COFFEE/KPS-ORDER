CREATE TABLE fuel_exceptions (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id),
 driver_id text NOT NULL REFERENCES users(id), manager_shift_id text NOT NULL REFERENCES shifts(id),
 shift_id text REFERENCES shifts(id), phase text NOT NULL CHECK(phase IN ('start','end')),
 reason text NOT NULL CHECK(reason IN ('aldrees_unavailable','aldrees_balance_empty','aldrees_limit_reached')),
 fuel_percent integer NOT NULL CHECK(fuel_percent>=0 AND fuel_percent<100),
 odometer_km integer NOT NULL CHECK(odometer_km>=0), vehicle jsonb NOT NULL,
 status text NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','approved','rejected','superseded','consumed')),
 created_at timestamptz NOT NULL DEFAULT now(),decided_by text REFERENCES users(id),decided_at timestamptz,
 consumed_shift_id text REFERENCES shifts(id),consumed_at timestamptz,
 CHECK((phase='end')=(shift_id IS NOT NULL))
);
CREATE INDEX fuel_exceptions_inbox ON fuel_exceptions(brand_id,manager_shift_id,status);
