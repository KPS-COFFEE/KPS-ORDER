-- Owner-authorized informational destination; never replaces login email or OTP identity.
CREATE TABLE IF NOT EXISTS manager_notification_subscriptions (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id),
 brand_id text NOT NULL REFERENCES brands(id), recipient_email text NOT NULL,
 enabled boolean NOT NULL DEFAULT true, actions boolean NOT NULL DEFAULT true,
 conversations boolean NOT NULL DEFAULT true, shift_reports boolean NOT NULL DEFAULT true,
 daily_reports boolean NOT NULL DEFAULT true, revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 configured_by text REFERENCES users(id), configured_source text NOT NULL DEFAULT 'owner_ui', created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), scanned_at timestamptz,
 UNIQUE(user_id,brand_id)
);
CREATE TABLE IF NOT EXISTS manager_email_notifications (
 id text PRIMARY KEY, subscription_id text NOT NULL REFERENCES manager_notification_subscriptions(id),
 recipient_user_id text NOT NULL REFERENCES users(id), brand_id text NOT NULL REFERENCES brands(id),
 subscription_revision integer NOT NULL, event_key text NOT NULL,
 category text NOT NULL CHECK(category IN ('action','conversation','shift_report','daily_report')),
 resource_id text NOT NULL, resource_version text NOT NULL DEFAULT '',
 email_outbox_id text REFERENCES email_outbox(id), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(subscription_id,subscription_revision,event_key)
);
CREATE INDEX IF NOT EXISTS manager_notification_scan ON manager_notification_subscriptions(scanned_at NULLS FIRST) WHERE enabled;
CREATE INDEX IF NOT EXISTS manager_email_outbox ON manager_email_notifications(email_outbox_id) WHERE email_outbox_id IS NOT NULL;

-- Explicit owner directive: notification email only, never login identity.
-- A mismatch leaves configuration pending rather than binding an unrelated person.
INSERT INTO manager_notification_subscriptions
 (id,user_id,brand_id,recipient_email,configured_source)
 SELECT 'mnsub_mohamed_20261031_'||u.brand_id,u.id,u.brand_id,
        'mohamedmeaa021@gmail.com','owner_directive_20261031'
 FROM users u JOIN brands b ON b.id=u.brand_id AND b.active
 WHERE u.id='staff_a9c2cb536601498aa89edc6b9fc6fef8' AND u.active
   AND u.role='delivery_manager' AND u.display_name='محمد عبدالسميع'
   AND u.brand_id IN ('kps','enbishan')
 ON CONFLICT(user_id,brand_id) DO NOTHING;
DO $directive$
BEGIN
 IF to_regclass('staff_brand_memberships') IS NOT NULL THEN
  EXECUTE $seed$
   INSERT INTO manager_notification_subscriptions
    (id,user_id,brand_id,recipient_email,configured_source)
   SELECT 'mnsub_mohamed_20261031_'||m.brand_id,u.id,m.brand_id,
          'mohamedmeaa021@gmail.com','owner_directive_20261031'
   FROM users u JOIN staff_brand_memberships m ON m.user_id=u.id AND m.active
   JOIN brands b ON b.id=m.brand_id AND b.active
   WHERE u.id='staff_a9c2cb536601498aa89edc6b9fc6fef8' AND u.active
     AND u.role='delivery_manager' AND u.display_name='محمد عبدالسميع'
     AND m.brand_id IN ('kps','enbishan') AND jsonb_array_length(m.branch_ids)>0
   ON CONFLICT(user_id,brand_id) DO NOTHING
  $seed$;
 END IF;
END
$directive$;

-- Supervisory report grants are applied through the authenticated owner
-- permission workflow after deployment. Startup never rewrites preserved users.
