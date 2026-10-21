"""Full-result dispatch counters and canonical current-shift membership."""
import pytest
from app import db
from test_operations import ops

pytestmark = pytest.mark.postgres


def test_workspace_counts_are_not_page_length_and_keep_overnight_work(ops):
    actor, post, order, shifts = ops
    mid, sid = shifts()
    old = order(name='workspace-old-complete',source='delivery_center')
    carried = order(name='workspace-overnight-task')
    with db.tx() as c:
        c.execute("UPDATE orders SET delivery_status='delivered',completed_at=now() WHERE id=%s",(old,))
        c.execute("UPDATE orders SET created_at=now()-interval '3 days',delivery_status='picked_up' WHERE id=%s",(carried,))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES('workspace-task',%s,'ops-driver',%s,'picked_up','ops-manager')",(carried,sid))
    for index in range(102):
        order(name='workspace-unassigned-'+str(index),source='delivery_center' if index % 2 == 0 else None)
    data = actor('manager').get('/api/orders?dispatch_workspace=true&limit=2').json()
    assert len(data['orders']) == 2
    assert data['workspace_scope'] == 'current'
    assert data['workspace_counts']['all']['total'] == 104
    assert data['workspace_counts']['current']['total'] == 103
    assert data['workspace_counts']['all']['sources'] == {'manager':52,'website':52}
    assert data['workspace_counts']['current']['sources'] == {'manager':51,'website':52}
    assert data['workspace_counts']['all']['delivered'] == 1
    assert data['workspace_counts']['current']['delivered'] == 0
    current = actor('manager').get('/api/orders?dispatch_workspace=true&dispatch_stage=road').json()
    assert [r['id'] for r in current['orders']] == [carried]
    assert current['workspace_counts']['current']['total'] == 103
    all_done = actor('manager').get('/api/orders?dispatch_workspace=true&dispatch_scope=all&dispatch_stage=delivered').json()
    assert [r['id'] for r in all_done['orders']] == [old]
    active = actor('manager').get('/api/orders?dispatch_workspace=true&dispatch_scope=all&dispatch_stage=active&limit=100').json()
    assert len(active['orders']) == 100
    assert old not in {r['id'] for r in active['orders']}
    assert active['workspace_counts']['all']['delivered'] == 1
    assert active['next_offset'] is not None
    own = actor('owner').get('/api/orders?dispatch_workspace=true').json()
    assert own['workspace_counts']['current']['total'] == 103
    empty = actor('manager').get('/api/orders?dispatch_workspace=true&dispatch_city=missing').json()
    assert empty['workspace_counts']['all']['total'] == 0 and not empty['orders']
    other = actor('other').get('/api/orders?dispatch_workspace=true').json()
    assert other['workspace_counts']['all']['total'] == 0
    assert other['workspace_counts']['all']['sources'] == {'manager':0,'website':0}
    assert empty['workspace_counts']['current']['sources'] == {'manager':0,'website':0}


def test_workspace_no_shift_is_empty_and_customer_is_forbidden(ops):
    actor, post, order, shifts = ops
    order()
    data = actor('manager').get('/api/orders?dispatch_workspace=true').json()
    assert data['orders'] == []
    assert data['workspace_counts']['all']['total'] == 1
    assert data['workspace_counts']['current']['total'] == 0
    assert data['workspace_counts']['all']['sources'] == {'manager':0,'website':1}
    assert data['workspace_counts']['current']['sources'] == {'manager':0,'website':0}
    assert actor('customer').get('/api/orders?dispatch_workspace=true').status_code == 403
    assert actor('driver').get('/api/orders?dispatch_workspace=true').status_code == 403


def test_source_counts_exclude_unauthorized_branches_and_other_services(ops):
    actor, post, order, shifts = ops
    shifts()
    website = order()
    order(service='pickup')
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('source-hidden',2,'source-hidden','ops-brand','فرع محجوب','رفحاء','{}')")
    order(source='delivery_center',branch='source-hidden')
    path = '/api/orders?dispatch_workspace=true&dispatch_scope=all'
    counts = actor('manager').get(path).json()['workspace_counts']['all']
    assert counts['total'] == 1
    assert counts['sources'] == {'manager':0,'website':1}
    assert actor('owner').get(path).json()['workspace_counts']['all']['sources'] == {'manager':1,'website':1}
    assert actor('manager').get(path+'&branch_id=source-hidden').json()['workspace_counts']['all']['sources'] == {'manager':0,'website':0}
