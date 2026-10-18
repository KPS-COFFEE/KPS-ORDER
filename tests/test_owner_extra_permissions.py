"""Owner-approved supplementary powers; synthetic SMS, isolated PostgreSQL only."""
import pytest
from app import db, employee_permissions as policy
from tests.test_core import _make_user
from tests.sms_policy_helpers import sms_login, sms_step_up


def test_grants_are_explicit_allowlisted_and_never_change_the_role():
    driver={'role':'driver','permissions':['accounting_review']}
    legacy=policy.effective(driver)
    assert policy.effective(dict(driver,extra_permissions=[]))==legacy
    granted=dict(driver,extra_permissions=['branch_printers_manage','accounting_review','staff_catalog_view','unknown'])
    assert policy.effective(granted)==legacy|{'branch_printers_manage'}
    assert granted['role']=='driver'
    assert policy.effective(dict(granted,permission_denials=['branch_printers_manage']))==legacy
    assert policy.effective({'role':'customer','extra_permissions':['branch_printers_manage']})==set()
    assert policy.grantable('owner')==set()
    assert not policy.grantable('driver') & {'refunds_manage','shifts_approve','accounting_review','payments_collect'}
    catalog={p['code']:p for p in policy.catalog()}
    assert 'driver' in catalog['branch_printers_manage']['delegable_roles']
    assert catalog['shifts_approve']['delegation_status']=='role_policy_required'
    assert not catalog['accounting_review']['delegable_roles']


def _save(client,target,values,expected=None):
    return client.put('/api/identity/staff/'+target['id']+'/permissions',json={
        'enabled_permissions':sorted(values),'expected_permissions':sorted(policy.effective(target) if expected is None else expected),
        'reason':'Owner reviewed scoped supplementary responsibility'})


@pytest.mark.postgres
def test_owner_sms_grant_printers_preserves_role_branches_and_immediate_revoke(client,monkeypatch):
    driver=_make_user('driver',branches=['kps-branch-1'])
    owner=_make_user('owner')
    sms_login(client,driver,monkeypatch)
    driver_cookies=dict(client.cookies);driver_csrf=client.headers['X-CSRF-Token']
    assert client.get('/api/printing/devices').status_code==403
    sms_login(client,owner,monkeypatch)
    original=policy.effective(driver)
    assert _save(client,driver,original|{'branch_printers_manage'}).status_code==403
    sms_step_up(client,owner,monkeypatch)
    result=_save(client,driver,original|{'branch_printers_manage'})
    assert result.status_code==200,result.text
    updated=result.json()['user']
    assert updated['extra_permissions']==['branch_printers_manage']
    assert updated['role']=='driver' and updated['branch_ids']==driver['branch_ids']
    assert updated['permissions']==driver.get('permissions',[])
    owner_cookies=dict(client.cookies);owner_csrf=client.headers['X-CSRF-Token']
    client.cookies.clear();client.cookies.update(driver_cookies);client.headers['X-CSRF-Token']=driver_csrf
    assert client.get('/api/printing/devices').status_code==200
    assert client.post('/api/printing/devices',json={'branch_id':'kps-branch-2','label':'Forbidden'}).status_code==404
    permitted=client.post('/api/printing/devices',json={'branch_id':'kps-branch-1','label':'Synthetic test printer'})
    assert permitted.status_code==200,permitted.text
    assert client.get('/api/identity/staff').status_code==403
    assert _save(client,driver,[]).status_code==403
    client.cookies.clear();client.cookies.update(owner_cookies);client.headers['X-CSRF-Token']=owner_csrf
    assert _save(client,updated,original).status_code==200
    assert _save(client,updated,original).status_code==409 # stale expected values
    with db.tx() as conn:
        audit=conn.execute("SELECT details FROM audit_events WHERE action='staff.permissions.updated' AND entity_id=%s ORDER BY created_at",(driver['id'],)).fetchall()
        assert len(audit)==2 and audit[0]['details']['extra_permissions']==['branch_printers_manage']
        assert audit[1]['details']['extra_permissions']==[]
    client.cookies.clear();client.cookies.update(driver_cookies);client.headers['X-CSRF-Token']=driver_csrf
    assert client.get('/api/session').status_code==200 # same live session
    assert client.get('/api/printing/devices').status_code==403
    assert client.post('/api/printing/devices',json={'branch_id':'kps-branch-1','label':'Revoked replay'}).status_code==403


@pytest.mark.postgres
def test_catalog_visibility_is_separate_from_granting_and_personnel_access(client,monkeypatch):
    manager=_make_user('delivery_manager',branches=['kps-branch-1'])
    owner=_make_user('owner')
    sms_login(client,manager,monkeypatch)
    assert client.get('/api/identity/permissions/catalog').status_code==403
    sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    result=_save(client,manager,policy.effective(manager)|{'staff_catalog_view'})
    assert result.status_code==200,result.text
    sms_login(client,manager,monkeypatch)
    catalog=client.get('/api/identity/permissions/catalog')
    assert catalog.status_code==200 and catalog.json()['can_grant'] is False
    assert set(p['code'] for p in catalog.json()['permission_catalog'])==set(policy.REGISTRY)
    assert client.get('/api/identity/staff').status_code==403
    assert _save(client,manager,[]).status_code==403


@pytest.mark.postgres
def test_financial_exclusive_unknown_and_cross_brand_extra_grants_fail_closed(client,monkeypatch):
    target=_make_user('driver',branches=['kps-branch-1'])
    owner=_make_user('owner');sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    before=policy.effective(target)
    for forbidden in ('shifts_approve','refunds_manage','accounting_review','owner','users_manage','unknown'):
        assert _save(client,target,before|{forbidden}).status_code==422
    assert _save(client,owner,[]).status_code==409
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('extra-other-brand','Other')")
        conn.execute("UPDATE users SET brand_id='extra-other-brand' WHERE id=%s",(target['id'],))
    assert _save(client,target,before|{'branch_printers_manage'}).status_code==404


@pytest.mark.postgres
def test_sms_staff_optional_fields_mobile_change_and_lifecycle(client,monkeypatch):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    invited=client.post('/api/identity/staff/invitations',json={'display_name':'Synthetic staff only','phone':'+966500901111','role':'driver','branch_ids':['kps-branch-1']})
    assert invited.status_code==200,invited.text
    target=invited.json()['user'];path='/api/identity/staff/'+target['id']
    assert target['active'] and not target['phone_verified_at'] and not target['phone_sms_verified_at']
    assert target['email'] is None and not target['employee_ref']
    assert client.post('/api/identity/staff/invitations',json={'display_name':'Duplicate','phone':target['phone'],'role':'driver','branch_ids':['kps-branch-1']}).status_code==409
    assert client.post('/api/identity/staff/invitations',json={'display_name':'No fake POS','phone':'+966500901112','role':'cashier','branch_ids':['kps-branch-1']}).status_code==422
    for change in ({'active':False},{'active':True},{'branch_ids':['kps-branch-1','kps-branch-2']}):
        result=client.patch(path,json={**change,'reason':'Synthetic owner staff lifecycle test'})
        assert result.status_code==200,result.text
    with db.tx() as conn:
        conn.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s',(target['id'],))
    result=client.patch(path,json={'phone':'+966500901113','reason':'Owner registered replacement mobile; staff must prove it'})
    assert result.status_code==200,result.text
    assert result.json()['user']['phone']=='+966500901113'
    assert result.json()['user']['phone_verified_at'] is None
    assert result.json()['user']['phone_sms_verified_at'] is None
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM users WHERE id=%s',(target['id'],)).fetchone()['n']==1


@pytest.mark.postgres
def test_additional_branch_availability_is_scoped_and_revocation_blocks_replay(client,monkeypatch):
    target=_make_user('driver',branches=['kps-branch-1'])
    owner=_make_user('owner')
    with db.tx() as conn:
        pid=conn.execute("SELECT id FROM products WHERE brand_id='kps' ORDER BY id LIMIT 1").fetchone()['id']
        conn.execute("INSERT INTO brands(id,name) VALUES('stock-other-brand','Other')")
        conn.execute("INSERT INTO products(id,brand_id,name,category,price_halalas) VALUES('other-stock-product','stock-other-brand','Other stock','Test only',100)")
    path='/api/branches/kps-branch-1/products/'+pid+'/availability'
    sms_login(client,target,monkeypatch)
    target_cookies=dict(client.cookies);target_csrf=client.headers['X-CSRF-Token']
    assert client.post(path,json={'available':False}).status_code==403
    sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    grant=_save(client,target,policy.effective(target)|{'branch_availability'})
    assert grant.status_code==200,grant.text
    owner_cookies=dict(client.cookies);owner_csrf=client.headers['X-CSRF-Token']
    client.cookies.clear();client.cookies.update(target_cookies);client.headers['X-CSRF-Token']=target_csrf
    changed=client.post(path,json={'available':False,'reason':'Stock unavailable at authorized branch'},headers={'Idempotency-Key':'scoped-stock-one'})
    assert changed.status_code==200,changed.text
    assert client.post('/api/branches/kps-branch-2/products/'+pid+'/availability',json={'available':False}).status_code==404
    assert client.post('/api/branches/kps-branch-1/products/other-stock-product/availability',json={'available':False}).status_code==404
    # An availability grant is not permission to change the menu globally.
    assert client.patch('/api/admin/products/'+pid,json={'name':'Forbidden global edit'}).status_code==403
    with db.tx() as conn:
        row=conn.execute('SELECT settings FROM products WHERE id=%s',(pid,)).fetchone()
        assert 'kps-branch-1' in row['settings']['unavailable_branch_ids']
        assert 'kps-branch-2' not in row['settings']['unavailable_branch_ids']
    client.cookies.clear();client.cookies.update(owner_cookies);client.headers['X-CSRF-Token']=owner_csrf
    assert _save(client,grant.json()['user'],policy.effective(target)).status_code==200
    client.cookies.clear();client.cookies.update(target_cookies);client.headers['X-CSRF-Token']=target_csrf
    assert client.post(path,json={'available':False,'reason':'Stock unavailable at authorized branch'},headers={'Idempotency-Key':'scoped-stock-one'}).status_code==403
