ALTER TABLE fuel_exceptions DROP CONSTRAINT fuel_exceptions_phase_check;
ALTER TABLE fuel_exceptions DROP CONSTRAINT fuel_exceptions_check;
ALTER TABLE fuel_exceptions ADD CHECK(phase IN ('start','end','activation'));
ALTER TABLE fuel_exceptions ADD CHECK((phase!='start')=(shift_id IS NOT NULL));
