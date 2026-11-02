-- External source invoices are imported without inventing a customer phone or
-- actual delivery/collection time. This is not a second issued fiscal invoice.
CREATE TABLE legacy_invoice_imports (
 id text PRIMARY KEY,
 brand_id text NOT NULL REFERENCES brands(id), branch_id text NOT NULL REFERENCES branches(id),
 source text NOT NULL CHECK(source='Sabad'), invoice_reference text NOT NULL,
 invoice_issued_at timestamptz NOT NULL, invoice_date date NOT NULL,
 order_id text NOT NULL UNIQUE REFERENCES orders(id),
 original_shift_id text NOT NULL REFERENCES shifts(id), manager_shift_id text NOT NULL REFERENCES shifts(id),
 ledger_shift_id text NOT NULL UNIQUE REFERENCES shifts(id), grant_id text NOT NULL REFERENCES prior_delivery_grants(id),
 actor_id text NOT NULL REFERENCES users(id), operational_date date NOT NULL,
 total_halalas bigint NOT NULL CHECK(total_halalas>0), tax_halalas bigint NOT NULL CHECK(tax_halalas>=0 AND tax_halalas<=total_halalas),
 cash_halalas bigint NOT NULL CHECK(cash_halalas>=0), card_halalas bigint NOT NULL CHECK(card_halalas>=0),
 reason text NOT NULL CHECK(length(btrim(reason))>=5), details jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 CHECK(cash_halalas+card_halalas=total_halalas),
 UNIQUE(brand_id,branch_id,source,invoice_date,invoice_reference)
);
CREATE TRIGGER immutable_legacy_invoice_imports BEFORE UPDATE OR DELETE ON legacy_invoice_imports
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
