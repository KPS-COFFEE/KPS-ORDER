-- Dedicated task conversation: drivers cannot read the customer's support inbox.
ALTER TABLE conversations ADD COLUMN driver_task_id text REFERENCES driver_tasks(id);
CREATE UNIQUE INDEX conversation_driver_task ON conversations(driver_task_id) WHERE driver_task_id IS NOT NULL;
