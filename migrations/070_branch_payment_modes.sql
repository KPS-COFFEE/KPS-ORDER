-- Schema capability only. No activation, seller mapping or historical rewrite.
ALTER TABLE branch_payment_attempts DROP CONSTRAINT branch_payment_attempts_mode_check;
ALTER TABLE branch_payment_attempts ADD CONSTRAINT branch_payment_attempts_mode_check
 CHECK(mode IN ('test','live'));
ALTER TABLE branch_online_receipts DROP CONSTRAINT branch_online_receipts_mode_check;
ALTER TABLE branch_online_receipts ADD CONSTRAINT branch_online_receipts_mode_check
 CHECK(mode IN ('test','live'));
