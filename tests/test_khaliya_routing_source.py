"""Pure routing/pricing tests. SQL persistence is covered separately in PostgreSQL acceptance."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from app import catalog, orders
from tests.test_launch_branches import branch as sample_branch


def fixtures():
    branches={}
    for no in (1,2):
        b=sample_branch(no);b.update(id=f'kps-branch-{no}',brand_id='kps')
        branches[b['id']]=b
    products={}
    for pid in ('kps-01','kps-33'):
        products[pid]={'id':pid,'brand_id':'kps','name':pid,'category':'TEST','price_halalas':1950,
                       'settings':{'enabled':True,'source_verified':True}}
    products['kps-33']['settings']['available_branch_ids']=['kps-branch-2']
    return branches,products


class Rows:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


class CatalogConnection:
    def __init__(self,branches,products):self.branches=branches;self.products=products
    def execute(self,sql,args=None):
        if 'FROM brands' in sql:return Rows({'id':'kps','settings':{}})
        if "branch_no=2" in sql:return Rows(self.branches.get('kps-branch-2'))
        if 'FROM branches' in sql:return Rows(self.branches.get(args[0]))
        if 'FROM products' in sql:return Rows(self.products.get(args[0]))
        raise AssertionError(sql)


def quote_body(service='delivery',branch='kps-branch-1',items=None):
    return catalog.QuoteBody.model_validate({'brand_id':'kps','branch_id':branch,'service':service,'payment_method':'cash',
        'items':items or [{'product_id':'kps-01','quantity':2},{'product_id':'kps-33','quantity':1}],
        'customer':{'name':'TEST','neighborhood':'TEST حي','address':'TEST','location':{'lat':28,'lng':45},
                    'car':{'color':'black','plate':'TEST'}}})


def test_quote_routes_all_items_preserves_request_and_selected_delivery_fee():
    branches,products=fixtures();branches['kps-branch-2']['settings']['delivery_fee_halalas']=900
    original=deepcopy((branches,products));body=quote_body()
    internal=catalog.quote_order(CatalogConnection(branches,products),body,True)
    assert internal['branch_id']=='kps-branch-2' and len(internal['items'])==2
    assert internal['total_halalas']==6050 and internal['delivery_fee_halalas']==200
    assert body.branch_id=='kps-branch-1' and (branches,products)==original
    public=catalog.customer_quote_view(internal)
    assert public['branch_id']=='kps-branch-1' and 'internal_routing' not in public
    assert internal['internal_routing']['reason']=='khaliya_branch_2'


@pytest.mark.parametrize('service',['pickup','car'])
def test_explicit_pickup_switch_required_even_for_direct_api(service):
    branches,products=fixtures();conn=CatalogConnection(branches,products)
    with pytest.raises(HTTPException) as error:catalog.quote_order(conn,quote_body(service))
    assert error.value.detail['message']=='الخلية متوفرة للاستلام من فرع 2 فقط.'
    result=catalog.quote_order(conn,quote_body(service,'kps-branch-2'))
    assert result['branch_id']=='kps-branch-2' and 'internal_routing' not in result


@pytest.mark.parametrize('mode',['closed','missing','wrong_city','stock','other_item','outside_coverage','payment'])
def test_routing_cannot_bypass_target_or_selected_customer_gates(mode):
    branches,products=fixtures();target=branches['kps-branch-2']
    if mode=='closed':target['settings']['active']=False
    elif mode=='missing':del branches['kps-branch-2']
    elif mode=='wrong_city':target['city']='رفحاء'
    elif mode=='stock':target['settings']['product_availability']={'kps-33':False}
    elif mode=='other_item':target['settings']['product_availability']={'kps-01':False}
    elif mode=='outside_coverage':branches['kps-branch-1']['settings']['coverage']={'type':'radius','center':{'lat':29,'lng':46},'radius_meters':100}
    elif mode=='payment':target['settings']['payment_methods']=['card']
    with pytest.raises(HTTPException):catalog.quote_order(CatalogConnection(branches,products),quote_body())


def test_unrelated_orders_and_other_cities_keep_their_existing_path():
    branches,products=fixtures();conn=CatalogConnection(branches,products)
    normal=catalog.quote_order(conn,quote_body(items=[{'product_id':'kps-01','quantity':1}]))
    assert normal['branch_id']=='kps-branch-1' and 'internal_routing' not in normal
    branches['kps-branch-1']['city']='رفحاء'
    with pytest.raises(HTTPException) as error:catalog.quote_order(conn,quote_body())
    assert 'حفر الباطن' in error.value.detail['message']


def test_customer_projection_does_not_mutate_staff_execution_snapshot():
    branches,products=fixtures();internal=catalog.quote_order(CatalogConnection(branches,products),quote_body())
    row={'branch_id':'kps-branch-2','branch_name':'TEST 2','branch_no':2,'snapshot':internal,'customer':{'name':'TEST','risk_alert':True},'performance_snapshot':{}}
    before=deepcopy(row);public=orders.customer_order_view(row)
    assert public['branch_id']=='kps-branch-1' and public['branch_no']==1
    assert 'internal_routing' not in public['snapshot'] and 'risk_alert' not in public['customer']
    assert row==before


def test_nearest_respects_pickup_branch_and_delivery_target_stock():
    branches,products=fixtures();rows=list(branches.values());ps=list(products.values())
    pickup=catalog.rank_nearest_branches(rows,{'lat':28,'lng':45},'pickup',{},ps,products)
    assert [b['branch_no'] for b in pickup['branches']]==[2]
    delivery=catalog.rank_nearest_branches(rows,{'lat':28,'lng':45},'delivery',{},ps,products)
    assert {b['branch_no'] for b in delivery['branches']}=={1,2}
    branches['kps-branch-2']['settings']['product_availability']={'kps-01':False}
    assert not catalog.rank_nearest_branches(rows,{'lat':28,'lng':45},'delivery',{},ps,products)['branches']
