"""Destination intent cannot become financial data or fabricated cross-brand tasks."""
from datetime import datetime, timezone, timedelta
import pytest
from app import driver_destination as dest, db
from test_driver_locations import gps_client, gps_data


class Rows:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows


class Context:
    def __init__(self, city='حفر الباطن', tasks=None, trip=True, returned_at=None):
        self.city, self.tasks, self.trip, self.returned_at = city, tasks or [], trip, returned_at
    def execute(self, sql, args):
        if 'FROM branches' in sql:
            return Rows([{'id':'b','name':'Branch','city':self.city,'brand_id':'kps',
                          'settings':{'location':{'lat':28.,'lng':45.},'location_verified':True}}] if 'b' in args[1] else [])
        if 'FROM driver_tasks' in sql: return Rows(self.tasks)
        if 'FROM driver_work_trips' in sql: return Rows([{'shift_id':'s','departure_branch_id':'b' if self.trip else None,'latest_returned_at':self.returned_at}])
        raise AssertionError(sql)


def state(city='حفر الباطن', saved=None, tasks=None, trip=True, viewer=None, shift_branches=None, returned_at=None):
    user = viewer or {'role':'delivery_manager','brand_id':'kps','branch_ids':['b']}
    row = {'id':'d','shift_id':'s','branch_ids':['b'],'shift_branches':shift_branches or ['b'],'next_destination':saved or {}}
    return dest.metadata(Context(city,tasks,trip,returned_at),user,[row])['d']


def task(**changes):
    return {'driver_id':'d','shift_id':'s','status':'picked_up','completed_at':None,'delivery_status':'picked_up',
            'preparation_status':'ready','brand_id':'kps','branch_id':'b','created_at':datetime.now(timezone.utc), **changes}


def test_outside_hafar_return_requires_current_trip_and_no_active_task():
    assert state('رفحاء')['next_destination']['source'] == 'departure_branch'
    assert state('رفحاء',trip=False)['next_destination'] is None
    result = state('رفحاء', tasks=[task()])
    assert result['next_destination'] is None
    assert result['active_order_brand'] == {'id':'kps','name':'KPS','source':'active_task'}
    hidden = state('رفحاء',tasks=[task(branch_id='hidden')])
    assert hidden['next_destination'] is None and hidden['active_order_brand'] is None
    assert state('رفحاء',shift_branches=['b','hidden'])['next_destination'] is None


def test_hafar_requires_explicit_intent_and_does_not_revive_superseded_declaration():
    assert state()['destination_choice_required']
    now = datetime.now(timezone.utc)
    saved = {'kind':'external_enbishan','declared_at':now.isoformat()}
    result = state(saved=saved)
    assert result['active_order_brand']['id'] == 'enbishan'
    assert result['active_order_brand']['source'] == 'driver_declared'
    assert 'location' not in result['next_destination']
    done = task(status='delivered',completed_at=now,delivery_status='delivered',created_at=now+timedelta(seconds=1))
    assert state(saved=saved,tasks=[done])['destination_choice_required']
    assert state(viewer={'role':'delivery_manager','brand_id':'kps','branch_ids':['other']},saved=saved)['active_order_brand'] is None
    branch = state(saved={'kind':'branch','branch_id':'b','declared_at':now.isoformat()})
    assert branch['next_destination']['branch_id']=='b' and branch['active_order_brand'] is None


@pytest.mark.parametrize('body',[{'kind':'branch'}, {'kind':'external_enbishan','branch_id':'b'}, {'kind':'fake'},
                                 {'kind':'branch','branch_id':'b','driver_id':'other'}])
def test_strict_destination_choice(body):
    with pytest.raises(ValueError): dest.DestinationChoice(**body)


@pytest.mark.postgres
def test_driver_destination_write_scope_and_no_financial_side_effect(gps_data):
    client,user=gps_data
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('gps-branch',99998,'gps-destination','kps','Test','حفر الباطن','{}')")
    endpoint='/api/operations/driver-destination'
    result=client.put(endpoint,json={'kind':'external_enbishan'})
    assert result.status_code==200,result.text
    assert result.json()['active_order_brand']['source']=='driver_declared'
    with db.tx() as c:
        row=c.execute("SELECT next_destination,external_enbishan FROM shifts WHERE id='gps-shift'").fetchone()
        assert row['next_destination']['kind']=='external_enbishan' and row['external_enbishan']=={}
    assert client.put(endpoint,json={'kind':'branch','branch_id':'forbidden'}).status_code==403
    assert client.put(endpoint,json={'kind':'branch','branch_id':'gps-branch'}).status_code==200
    user['role']='delivery_manager'
    assert client.put(endpoint,json={'kind':'external_enbishan'}).status_code==403
    user['role']='driver'
    with db.tx() as c: c.execute("UPDATE shifts SET status='closed' WHERE id='gps-shift'")
    assert client.put(endpoint,json={'kind':'external_enbishan'}).status_code==409


@pytest.mark.parametrize('kind', ['branch', 'external_enbishan'])
def test_actual_return_supersedes_declared_destination(kind):
    declared = datetime.now(timezone.utc)
    saved = {'kind':kind,'declared_at':declared.isoformat()}
    if kind == 'branch': saved['branch_id'] = 'b'
    before = state(saved=saved,returned_at=declared-timedelta(seconds=1))
    assert before['next_destination']['source'] == 'driver_declared'
    if kind == 'external_enbishan':
        assert before['active_order_brand']['source'] == 'driver_declared'
    for returned in (declared, declared+timedelta(seconds=1)):
        after = state(saved=saved,trip=False,returned_at=returned)
        assert after['next_destination'] is None
        assert after['active_order_brand'] is None
        assert after['destination_choice_required']


@pytest.mark.parametrize('status,expected',[('picked_up','delivering'),('problem','delivering'),('assigned','awaiting_pickup'),('accepted','awaiting_pickup')])
def test_movement_state_is_scoped_task_state_not_physical_movement(status, expected):
    visible = state(tasks=[task(status=status)])
    assert visible['movement_state'] == expected
    assert visible['visible_active_task_count'] == 1
    hidden = state(tasks=[task(status=status,branch_id='hidden')])
    assert hidden['movement_state'] == 'awaiting_destination'
    assert hidden['visible_active_task_count'] == 0


def test_movement_state_for_confirmed_intent_sources():
    assert state('رفحاء')['movement_state'] == 'returning'
    assert state()['movement_state'] == 'awaiting_destination'
    declared = {'kind':'external_enbishan','declared_at':datetime.now(timezone.utc).isoformat()}
    assert state(saved=declared)['movement_state'] == 'external_enbishan'
