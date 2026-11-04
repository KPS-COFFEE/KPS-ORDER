-- A clarification changes only its own workflow, never the linked financial source.
CREATE TABLE financial_case_clarifications (
 id text PRIMARY KEY,
 order_id text NOT NULL REFERENCES orders(id),
 case_event_id text NOT NULL REFERENCES order_events(id),
 requested_by text NOT NULL REFERENCES users(id),
 assignee_id text NOT NULL REFERENCES users(id),
 question text NOT NULL CHECK(length(btrim(question)) BETWEEN 3 AND 2000),
 source_order_version integer NOT NULL,
 status text NOT NULL CHECK(status IN ('requested','accepted','responded','declined','resolved')),
 version integer NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX financial_clarification_order ON financial_case_clarifications(order_id,status);
CREATE TABLE financial_case_clarification_events (
 id text PRIMARY KEY,
 clarification_id text NOT NULL REFERENCES financial_case_clarifications(id),
 version integer NOT NULL CHECK(version>0),
 action text NOT NULL CHECK(action IN ('requested','accepted','responded','declined','returned','resolved','reassigned')),
 actor_id text NOT NULL REFERENCES users(id),
 assignee_id text NOT NULL REFERENCES users(id),
 from_status text,
 to_status text NOT NULL,
 note text NOT NULL CHECK(length(btrim(note)) BETWEEN 3 AND 2000),
 evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(clarification_id,version)
);
CREATE TRIGGER financial_clarification_events_immutable BEFORE UPDATE OR DELETE
 ON financial_case_clarification_events FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
