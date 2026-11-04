-- Preserve the exact recent-contact risk predicate without scanning/deTOASTing
-- every recent branch order. Basket equality remains a residual exact check;
-- do not index large receipt JSON or change customer ownership semantics.
CREATE INDEX orders_recent_contact
ON orders (brand_id, branch_id, (customer->>'phone'), created_at DESC);
