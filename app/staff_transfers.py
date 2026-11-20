"""Owner-authorized future branch moves; historical shifts remain immutable."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db

router = APIRouter(prefix='/api/staff-transfers', tags=['staff-transfers'])
# Existing identity verified in migration 132, explicitly delegated by the owner.
DELEGATE_ID = 'staff_a9c2cb536601498aa89edc6b9fc6fef8'


def allowed(user):
    return user.get('role') == 'owner' or (
        user.get('id') == DELEGATE_ID and user.get('role') == 'delivery_manager'
        and user.get('active', False) and not user.get('_morning_manager_context'))


def actor(user=Depends(auth.current_user)):
    if not allowed(user):
        raise db.error(403, 'STAFF_TRANSFER_FORBIDDEN', 'نقل الموظفين للمالك أو المفوض فقط.')
    return user


class Move(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    branch_ids: list[str] = Field(min_length=1, max_length=500)
    expected_branch_ids: list[str] = Field(max_length=500)
    reason: str = Field(min_length=3, max_length=500)


def target(c, user, identifier):
    row = c.execute("SELECT * FROM users WHERE id=%s AND brand_id=%s AND role NOT IN ('owner','customer') AND invitation_status<>'archived' FOR UPDATE", (identifier, user['brand_id'])).fetchone()
    if not row or (user['role'] != 'owner' and (not row['branch_ids'] or not set(row['branch_ids']) <= set(user.get('branch_ids', [])))):
        raise db.error(404, 'STAFF_NOT_FOUND', 'الموظف خارج نطاقك.')
    return row


@router.get('')
def listing(user=Depends(actor)):
    with db.tx() as c:
        rows=c.execute("SELECT id,display_name,role,branch_ids FROM users WHERE brand_id=%s AND role NOT IN ('owner','customer') AND invitation_status<>'archived' ORDER BY display_name,id", (user['brand_id'],)).fetchall()
        return {'users':[r for r in rows if user['role']=='owner' or (r['branch_ids'] and set(r['branch_ids'])<=set(user.get('branch_ids',[])))]}


@router.post('/{identifier}')
def move(identifier: str, body: Move, user=Depends(actor)):
    with db.tx() as c:
        # Same admission lock used by operations.open_shift_in_tx.
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('shift:'+identifier,))
        row=target(c,user,identifier)
        if sorted(row['branch_ids']) != sorted(body.expected_branch_ids):
            raise db.error(409,'STAFF_BRANCH_SCOPE_STALE','تغيرت فروع الموظف؛ حدّث الشاشة.')
        scope=auth._staff_scope(c,user,body.branch_ids)
        # Locking the employee serializes against opening a new driver shift.
        if c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed' AND NOT (status='submitted' AND to_jsonb(shifts)->>'staffing_ended_at' IS NOT NULL) LIMIT 1",(identifier,)).fetchone():
            raise db.error(409,'STAFF_OPEN_SHIFT','أقفل شفت الموظف قبل نقله.')
        auth.ensure_safe_staff_scope_change(c,row,scope,row['active'],owner_scope_change=True)
        c.execute('UPDATE users SET branch_ids=%s,updated_at=now() WHERE id=%s',(db.json(scope),identifier))
        c.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL',(identifier,))
        db.audit(c,user['id'],'staff.branches.transferred','user',identifier,{'before':row['branch_ids'],'after':scope,'reason':body.reason,'historical_shifts_changed':False})
        return {'status':'transferred','user_id':identifier,'branch_ids':scope}
