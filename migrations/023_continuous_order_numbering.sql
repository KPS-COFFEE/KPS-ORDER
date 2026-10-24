-- Continuous per-branch sequence; the receipt date follows the delivery operating day.
CREATE TABLE IF NOT EXISTS order_branch_counters (
 branch_id TEXT PRIMARY KEY REFERENCES branches(id),
 last_number BIGINT NOT NULL CHECK(last_number > 0)
);
-- Forward-safe initialization if daily numbers were used before the owner correction.
INSERT INTO order_branch_counters(branch_id,last_number)
 SELECT branch_id,sum(last_number) FROM order_daily_counters GROUP BY branch_id
 ON CONFLICT(branch_id) DO NOTHING;
