"""Recipient-specific fleet notices and append-only read acknowledgements."""
from . import db


def unread(c,user):
    return c.execute("SELECT a.id,a.created_at,a.details FROM audit_events a "
        "WHERE a.action='fleet.driver_notice' AND a.entity_type='user' AND a.entity_id=%s "
        "AND a.details->>'brand_id'=%s AND NOT EXISTS (SELECT 1 FROM audit_events seen "
        "WHERE seen.action='fleet.driver_notice.read' AND seen.actor_id=%s "
        "AND seen.entity_type='fleet_notice' AND seen.entity_id=a.id) "
        "ORDER BY a.created_at DESC,a.id DESC LIMIT 50",(user['id'],user['brand_id'],user['id'])).fetchall()


def require_notice(c,user,nid):
    row=c.execute("SELECT id FROM audit_events WHERE id=%s AND action='fleet.driver_notice' "
        "AND entity_type='user' AND entity_id=%s AND details->>'brand_id'=%s",
        (nid,user['id'],user['brand_id'])).fetchone()
    if not row:raise db.error(404,'FLEET_NOTICE_NOT_FOUND','الإشعار غير موجود ضمن حسابك.')
    return row


def acknowledge(c,user,nid):
    # A read acknowledgement is not a driver signature or an operational approval.
    if not c.execute("SELECT 1 FROM audit_events WHERE action='fleet.driver_notice.read' "
        "AND actor_id=%s AND entity_type='fleet_notice' AND entity_id=%s",(user['id'],nid)).fetchone():
        db.audit(c,user['id'],'fleet.driver_notice.read','fleet_notice',nid,{'brand_id':user['brand_id']})
    return {'notice_id':nid,'read':True}
