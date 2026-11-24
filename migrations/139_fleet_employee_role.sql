ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK(role IN
 ('owner','admin','delivery_manager','delivery_staff','branch_manager','driver','customer','fleet_employee'));
