"""Central routing witnesses use real isolated PostgreSQL and canonical sessions."""
import pytest

from app import db
from test_enbishan_dual_brand import dual


@pytest.fixture
def central(dual, monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    return dual


@pytest.mark.postgres
def test_session_publishes_both_granted_branch_sets_without_projecting_user(central):
    _, actor, _, _, _ = central
    response = actor('manager').get('/api/session')
    assert response.status_code == 200, response.text
    user = response.json()['user']
    assert user['brand_id'] == 'kps'
    assert user['branch_ids'] == ['kps-branch-1']
    scopes = {b['id']: b for b in user['central_operations']['brands']}
    assert scopes['kps']['branch_ids'] == ['kps-branch-1']
    assert set(scopes['enbishan']['branch_ids']) == {'enbishan-test-1', 'enbishan-test-2'}


@pytest.mark.postgres
def test_resource_resolution_rechecks_membership_revocation(central):
    _, actor, _, _, order = central
    identifier = order('enbishan')
    path = '/api/central-operations/resource-context?kind=order&resource_id=' + identifier
    assert actor('manager').get(path).json() == {'brand_id': 'enbishan'}
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    assert actor('manager').get(path).status_code == 404
    session = actor('manager').get('/api/session').json()['user']
    assert [b['id'] for b in session['central_operations']['brands']] == ['kps']


@pytest.mark.postgres
def test_a_brand_membership_does_not_authorize_ungranted_branches(central):
    _, actor, _, _, order = central
    identifier = order('enbishan')
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-2\"]' WHERE user_id='dual-manager' AND brand_id='enbishan'")
    path = '/api/central-operations/resource-context?kind=order&resource_id=' + identifier
    assert actor('manager').get(path).status_code == 404


@pytest.mark.postgres
def test_unassigned_driver_cannot_resolve_customer_orders(central):
    _, actor, _, _, order = central
    identifier = order('enbishan')
    assert actor('driver').get('/api/central-operations/resource-context', params={
        'kind': 'order', 'resource_id': identifier,
    }).status_code == 404


@pytest.mark.postgres
@pytest.mark.parametrize('context_cookies,expected_status',[('true',401),('false',404)])
def test_customers_do_not_receive_or_resolve_staff_context(central,monkeypatch,context_cookies,expected_status):
    # Work routes reject customer credentials before endpoint authorization when
    # cookie isolation is enabled; legacy mode still exercises the 404 guard.
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED',context_cookies)
    _, actor, _, _, order = central
    identifier = order('enbishan')
    session = actor('enbishan-customer').get('/api/session').json()['user']
    assert 'central_operations' not in session
    assert actor('enbishan-customer').get('/api/central-operations/resource-context', params={
        'kind': 'order', 'resource_id': identifier,
    }).status_code == expected_status


@pytest.mark.postgres
def test_rollout_remains_off_until_explicitly_enabled(dual):
    _, actor, _, _, order = dual
    session = actor('manager').get('/api/session').json()['user']
    assert 'central_operations' not in session
    assert actor('manager').get('/api/central-operations/resource-context', params={
        'kind': 'order', 'resource_id': order('enbishan'),
    }).status_code == 404


@pytest.mark.postgres
def test_foreign_owner_cannot_resolve_or_grant_an_unassigned_branch(central):
    _, actor, _, _, _ = central
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",
                  (db.json(['enbishan-test-1']),))
    response=actor('owner').get('/api/central-operations/resource-context',params={
        'kind':'branch','resource_id':'enbishan-test-2'})
    assert response.status_code==404,response.text
    response=actor('owner').patch('/api/central-operations/staff/dual-driver',json={
        'branch_ids':['kps-branch-1','enbishan-test-2'],'reason':'Synthetic unauthorized branch grant'})
    assert response.status_code==403,response.text
    with db.tx() as c:
        assert set(c.execute("SELECT branch_ids FROM staff_brand_memberships WHERE user_id='dual-driver' AND brand_id='enbishan'").fetchone()['branch_ids'])=={'enbishan-test-1','enbishan-test-2'}
    response=actor('owner').post('/api/central-operations/staff/invitations',json={
        'display_name':'SYNTHETIC forbidden invite','phone':'+966500008765',
        'branch_ids':['enbishan-test-2']})
    assert response.status_code==403,response.text


@pytest.mark.postgres
def test_protected_print_link_uses_live_record_scope_and_explicit_header_wins(central):
    _, actor, _, _, order = central
    identifier=order('enbishan')
    path='/api/printing/orders/'+identifier+'/output'
    response=actor('manager').get(path)
    assert response.status_code==200,response.text
    assert actor('manager','kps').get(path).status_code==404
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    assert actor('manager').get(path).status_code==404


def test_side_leg_break_mutations_require_the_physical_anchor():
    from fastapi import HTTPException
    from app.driver_breaks import require_physical_shift
    require_physical_shift({'vehicle_log':{}})
    with pytest.raises(HTTPException) as denied:
        require_physical_shift({'vehicle_log':{'central_vehicle_shift_id':'physical'}})
    assert denied.value.status_code==409
    assert denied.value.detail['code']=='CENTRAL_VEHICLE_ANCHOR_REQUIRED'


def test_fleet_handover_rejects_unrelated_concurrent_shifts():
    from fastapi import HTTPException
    from app import fleet_management,central_workdays
    class Result:
        def fetchall(self):return [{'id':'a','status':'open'},{'id':'b','status':'open'}]
    class Connection:
        def execute(self,*args):return Result()
    from unittest.mock import patch
    with patch.object(central_workdays,'association',return_value=None):
        with pytest.raises(HTTPException) as denied:
            fleet_management.physical_driver_shift(Connection(),{'id':'owner'},'driver')
    assert denied.value.status_code==409
    assert denied.value.detail['code']=='ACTIVE_SWAP_OPEN_SHIFTS'


def test_fleet_handover_selects_authorized_physical_anchor():
    from app import fleet_management,central_workdays
    rows=[{'id':'side','status':'open'},{'id':'anchor','status':'open'}]
    class Result:
        def fetchall(self):return rows
    class Connection:
        def execute(self,*args):return Result()
    from unittest.mock import patch
    with patch.object(central_workdays,'association',return_value={'id':'day','anchor_shift_id':'anchor'}), patch.object(
            central_workdays,'authorized',return_value=({'id':'day'},[({},r) for r in rows])) as authorized:
        assert fleet_management.physical_driver_shift(Connection(),{'id':'owner'},'driver')['id']=='anchor'
        authorized.assert_called_once()


@pytest.mark.postgres
def test_central_fleet_cards_recheck_foreign_branch_scope(central):
    _,actor,_,_,_=central
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",(db.json(['enbishan-test-1']),))
        for identifier,brand,branch,plate in [('visible-kps','kps','kps-branch-1','TEST11'),('visible-enb','enbishan','enbishan-test-1','TEST22'),('hidden-enb','enbishan','enbishan-test-2','TEST33')]:
            c.execute('INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details) VALUES(%s,%s,%s,%s,%s)',
                      (identifier,brand,plate,plate,db.json({'management_branch_ids':[branch]})))
    response=actor('owner').get('/api/central-operations/fleet/vehicles')
    assert response.status_code==200,response.text
    cards={r['id']:r for r in response.json()['vehicles']}
    assert 'visible-kps' in cards and 'visible-enb' in cards
    assert cards['visible-enb']['brand_id']=='enbishan'
    assert 'hidden-enb' not in cards
    response=actor('owner').get('/api/central-operations/resource-context',params={'kind':'vehicle','resource_id':'hidden-enb'})
    assert response.status_code==404,response.text
    assert actor('owner','enbishan').get('/api/fleet/vehicles/hidden-enb').status_code==404
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    response=actor('owner').get('/api/central-operations/fleet/vehicles')
    assert response.status_code==200,response.text
    assert 'visible-enb' not in {r['id'] for r in response.json()['vehicles']}


def test_projected_owner_record_checks_keep_explicit_branch_limits():
    from app import auth,chat,reporting
    owner={'id':'owner','active':True,'role':'owner','brand_id':'enbishan','home_brand_id':'kps','branch_ids':['allowed']}
    order={'id':'order','brand_id':'enbishan','branch_id':'hidden'}
    conversation={'brand_id':'enbishan','branch_id':'hidden'}
    report={'brand_id':'enbishan','branch_ids':['allowed','hidden']}
    assert not auth.can_access_order(owner,order,None)
    assert not chat.allowed(owner,conversation)
    assert not reporting.can_read(owner,report)
    home=dict(owner);home.pop('home_brand_id')
    assert auth.can_access_order(home,order,None)
    assert chat.allowed(home,conversation)
    assert reporting.can_read(home,report)


def test_fleet_handover_never_bypasses_revoked_group_authority():
    from fastapi import HTTPException
    from app import fleet_management,central_workdays,db
    class Result:
        def fetchall(self):return [{'id':'side','status':'open'},{'id':'anchor','status':'open'}]
    class Connection:
        def execute(self,*args):return Result()
    from unittest.mock import patch
    with patch.object(central_workdays,'association',return_value={'id':'day','anchor_shift_id':'anchor'}),patch.object(
            central_workdays,'authorized',side_effect=db.error(404,'CENTRAL_WORKDAY_UNAVAILABLE','revoked')):
        with pytest.raises(HTTPException) as denied:
            fleet_management.physical_driver_shift(Connection(),{'id':'owner'},'driver')
    assert denied.value.status_code==404


@pytest.mark.postgres
def test_session_flag_off_exposes_only_authorized_existing_workday_drain(central,monkeypatch):
    from test_central_workdays import open_days
    _,actor,_,_,_=central
    manager,driver=open_days(central)
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','false')
    response=actor('driver').get('/api/session')
    assert response.status_code==200,response.text
    user=response.json()['user']
    assert 'central_operations' not in user
    assert len(user['central_workday_drain'])==1
    day=user['central_workday_drain'][0]
    assert day['anchor_shift_id']==driver['shift']['id']
    assert {leg['brand_id'] for leg in day['legs']}=={'kps','enbishan'}
    manager_user=actor('manager').get('/api/session').json()['user']
    assert {day['anchor_shift_id'] for day in manager_user['central_workday_drain']}=={manager['shift']['id'],driver['shift']['id']}
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    user=actor('driver').get('/api/session').json()['user']
    assert not user.get('central_workday_drain')
    assert 'central_workday_drain' not in actor('enbishan-customer').get('/api/session').json()['user']


@pytest.mark.postgres
def test_central_fleet_weighted_summary_filters_paging_and_export(central,monkeypatch):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED','true')
    from test_fleet import seed
    import io,zipfile
    _,actor,_,_,_=central
    with db.tx() as c:
        seed(c,'central-fleet-kps','CENTRAL KPS',10,driver='dual-driver',brand='kps',bids=['kps-branch-1'],km=100,liters=10)
        seed(c,'central-fleet-enb','CENTRAL ENB',10,driver='dual-driver',brand='enbishan',bids=['enbishan-test-1'],km=300,liters=20)
        seed(c,'central-fleet-hidden','CENTRAL HIDDEN',10,driver='dual-driver',brand='enbishan',bids=['enbishan-test-2'],km=700,liters=10)
    path='/api/central-operations/fleet/report'
    query={'start':'2026-10-10','end':'2026-10-10','limit':1}
    result=actor('owner').get(path,params=query)
    assert result.status_code==200,result.text
    assert result.json()['total']==3 and len(result.json()['rows'])==1
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",(db.json(['enbishan-test-1']),))
    result=actor('owner').get(path,params=query)
    assert result.status_code==200,result.text
    first=result.json()
    second=actor('owner').get(path,params={**query,'offset':1}).json()
    assert first['total']==2 and len(first['rows'])==1
    assert first['summary']['actual_distance']==400
    assert first['summary']['liters']==30
    assert first['summary']['efficiency']==13.333
    assert {first['rows'][0]['brand_id'],second['rows'][0]['brand_id']}=={'kps','enbishan'}
    assert first['summary']==second['summary']
    filtered=actor('owner').get(path,params={**query,'branch_id':'enbishan-test-1'}).json()
    assert filtered['total']==1 and filtered['rows'][0]['plate']=='CENTRAL ENB'
    options=actor('owner').get('/api/central-operations/fleet/options').json()
    assert 'enbishan-test-2' not in {b['id'] for b in options['branches']}
    assert len([d for d in options['drivers'] if d['id']=='dual-driver'])==1
    output=actor('owner').get('/api/central-operations/fleet/export',params={**query,'format':'xlsx','lang':'en'})
    assert output.status_code==200,output.text
    sheet=zipfile.ZipFile(io.BytesIO(output.content)).read('xl/worksheets/sheet1.xml').decode()
    assert 'CENTRAL KPS' in sheet and 'CENTRAL ENB' in sheet and 'CENTRAL HIDDEN' not in sheet
    assert 'Brand' in sheet and 'enbishan' in sheet
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    result=actor('owner').get(path,params=query).json()
    assert result['total']==1 and result['summary']['efficiency']==10
    assert actor('enbishan-customer').get(path,params=query).status_code==401


@pytest.mark.parametrize('hint',[None,'work','customer'])
def test_central_routes_choose_work_credentials_even_with_customer_hint(hint):
    from fastapi import Request
    from app import auth
    request=Request({'type':'http','method':'GET','path':'/api/central-operations/resource-context',
                     'query_string':b'','headers':[(b'x-kps-context',hint.encode())] if hint else []})
    assert auth.session_context(request)=='work'
