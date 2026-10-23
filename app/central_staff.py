"""One owner branch editor for existing shared employee identities."""
from fastapi import APIRouter, Depends, Request
from pydantic import Field
from . import auth, db, employee_permissions, multibrand
from .central_workdays import require_enabled

router = APIRouter(prefix='/staff', tags=['central-staff'])


def owner_scope(c, user, identifier):
    if user['role'] != 'owner':
        raise db.error(403,'ROLE_FORBIDDEN','إدارة صلاحيات الموظفين للمالك.')
    target=c.execute("SELECT * FROM users WHERE id=%s AND role<>'customer' FOR UPDATE",(identifier,)).fetchone()
    principal=multibrand.project(c,user,target['brand_id']) if target else None
    if not principal or (principal.get('home_brand_id') and not set(target['branch_ids'])<=set(principal.get('branch_ids',[]))):
        raise db.error(404,'STAFF_NOT_FOUND','الموظف خارج صلاحياتك.')
    return target,principal


def grants(c, target, allowed):
    output={target['brand_id']:list(target['branch_ids'])} if target['brand_id'] in allowed else {}
    for row in c.execute('SELECT brand_id,branch_ids FROM staff_brand_memberships WHERE user_id=%s AND active',
                         (target['id'],)).fetchall():
        if row['brand_id'] in allowed and row['brand_id'] != target['brand_id']:
            output[row['brand_id']]=row['branch_ids']
    return output


def display(c, target, allowed):
    item=auth.staff_public(c,target)
    item['branch_grants']=grants(c,target,allowed)
    item['branch_ids']=sorted({bid for values in item['branch_grants'].values() for bid in values})
    item['home_brand_id']=target['brand_id']
    return item


@router.get('')
def listing(user=Depends(auth.require_roles('owner'))):
    require_enabled(user,'drivers_manage')
    with db.tx() as c:
        allowed={b['id'] for b in multibrand.available(c,user)}
        rows=c.execute("SELECT * FROM users WHERE brand_id=ANY(%s) AND role<>'customer' ORDER BY role,display_name,id LIMIT 1000",(sorted(allowed),)).fetchall()
        return {'users':[display(c,row,allowed) for row in rows], 'permission_catalog':employee_permissions.catalog()}


class StaffUpdate(auth.StaffUpdateBody):
    expected_branch_ids: list[str] | None = Field(None,max_length=500)


def validate_branch_authority(c, user, rows):
    """A foreign owner membership grants explicit branches, not the whole brand."""
    for brand in {r['brand_id'] for r in rows}:
        principal=multibrand.project(c,user,brand)
        selected={r['id'] for r in rows if r['brand_id']==brand}
        if not principal or (principal.get('home_brand_id') and not selected<=set(principal.get('branch_ids',[]))):
            raise db.error(403,'BRANCH_SCOPE_FORBIDDEN','فرع خارج صلاحياتك.')


def scope_body(c, user, target, branch_ids, allowed):
    if len(branch_ids)!=len(set(branch_ids)):
        raise db.error(422,'BRANCH_SCOPE_INVALID','أزل تكرار الفروع.')
    rows=c.execute('SELECT id,brand_id FROM branches WHERE id=ANY(%s)',(branch_ids,)).fetchall()
    if {r['id'] for r in rows}!=set(branch_ids) or any(r['brand_id'] not in allowed for r in rows):
        raise db.error(403,'BRANCH_SCOPE_FORBIDDEN','فرع خارج صلاحياتك.')
    validate_branch_authority(c,user,rows)
    old=grants(c,target,allowed)
    validate_branch_authority(c,user,[{'id':bid,'brand_id':brand} for brand,ids in old.items() for bid in ids])
    scopes={brand:[] for brand in allowed}
    for row in rows:
        scopes[row['brand_id']].append(row['id'])
    old=grants(c,target,allowed)
    for brand,ids in scopes.items():
        projected=dict(target,brand_id=brand,branch_ids=old.get(brand,[]))
        auth.ensure_safe_staff_scope_change(c,projected,ids,target['active'])
    return scopes


@router.patch('/{identifier}')
def update(identifier:str,body:StaffUpdate,request:Request,user=Depends(auth.require_roles('owner'))):
    require_enabled(user,'drivers_manage')
    with db.tx() as c:
        auth.require_owner_step_up(c,user)
        target,principal=owner_scope(c,user,identifier)
        allowed={b['id'] for b in multibrand.available(c,user)}
        previous=grants(c,target,allowed)
        current={bid for bids in previous.values() for bid in bids}
        if body.expected_branch_ids is not None and set(body.expected_branch_ids)!=current:
            raise db.error(409,'STAFF_BRANCH_SCOPE_STALE','تغيرت فروع الموظف؛ حدّث الشاشة قبل الحفظ.')
        scopes=scope_body(c,user,target,body.branch_ids,allowed) if body.branch_ids is not None else None
        values=body.model_dump(exclude={'expected_branch_ids'},exclude_unset=True)
        if scopes is not None:values['branch_ids']=scopes[target['brand_id']]
        output=auth.update_staff_in_tx(c,identifier,auth.StaffUpdateBody(**values),principal)
        if scopes is not None:
            for brand,ids in scopes.items():
                if brand==target['brand_id']:continue
                c.execute('INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,active,granted_by) '
                    'VALUES(%s,%s,%s,%s,%s) ON CONFLICT(user_id,brand_id) DO UPDATE SET '
                    'branch_ids=EXCLUDED.branch_ids,active=EXCLUDED.active,granted_by=EXCLUDED.granted_by',
                    (identifier,brand,db.json(sorted(ids)),bool(ids),user['id']))
            db.audit(c,user['id'],'central.staff.branches','user',identifier,
                     {'before':previous,'after':scopes,'reason':body.reason})
        fresh=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
        return {'user':display(c,fresh,allowed)}


@router.post('/invitations')
def invite(body:auth.StaffInvitationBody,user=Depends(auth.require_roles('owner'))):
    require_enabled(user,'drivers_manage')
    with db.tx() as c:
        if not db.rate_limit(c,'staff-invite:'+user['id'],30,3600):
            raise db.error(429,'EMAIL_RATE_LIMIT','دعوات كثيرة. انتظر قليلًا.')
        allowed={b['id'] for b in multibrand.available(c,user)}
        rows=c.execute('SELECT id,brand_id FROM branches WHERE id=ANY(%s)',(body.branch_ids,)).fetchall()
        if len(body.branch_ids)!=len(set(body.branch_ids)) or {r['id'] for r in rows}!=set(body.branch_ids) or any(r['brand_id'] not in allowed for r in rows):
            raise db.error(403,'BRANCH_SCOPE_FORBIDDEN','فرع خارج صلاحياتك.')
        validate_branch_authority(c,user,rows)
        auth.require_owner_step_up(c,user)
        scopes={brand:sorted(r['id'] for r in rows if r['brand_id']==brand) for brand in allowed}
        home=c.execute('SELECT brand_id FROM users WHERE id=%s',(user['id'],)).fetchone()['brand_id']
        if not scopes.get(home) and rows:home=rows[0]['brand_id']
        principal=multibrand.project(c,user,home)
        if not principal:raise db.error(403,'ROLE_FORBIDDEN','البراند غير مخول.')
        output=auth.create_staff_invitation(c,body.model_copy(update={'branch_ids':scopes.get(home,[])}),principal)
        identifier=output['user']['id']
        for brand,ids in scopes.items():
            if brand!=home and ids:
                c.execute('INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,active,granted_by) VALUES(%s,%s,%s,true,%s)',
                          (identifier,brand,db.json(ids),user['id']))
        target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
        output['user']=display(c,target,allowed)
        db.audit(c,user['id'],'central.staff.invited','user',identifier,{'branch_grants':scopes})
        return output
