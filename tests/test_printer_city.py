import pytest
from app import db
from app.branch_printing import same_print_city
from test_operations import ops

@pytest.mark.parametrize('left,right,expected',[
    ('حفر الباطن','حفرالباطن',True),('رفحا','رفحاء',True),
    ('الخفجي','الخفجي',True),('الخفجي','رفحا',False),
    ('حفر الباطن','الخفجي',False),('مدينة جديدة','مدينة أخرى',False),
    ('','',False),('مدينة جديدة','مدينة جديدة',True)])
def test_receipt_city_aliases(left,right,expected):
    assert same_print_city({'city':left},{'city':right}) is expected

@pytest.mark.postgres
def test_printer_destinations_are_order_city_scoped(ops):
    actor,post,order,shifts=ops
    oid=order('delivery')
    cities={'same':'حفرالباطن','khafji':'الخفجي','rafha':'رفحا'}
    with db.tx() as c:
        c.execute("UPDATE branches SET city='حفر الباطن' WHERE id='ops-branch-other'")
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,printer_name,token_hash,approved_at,created_by) VALUES('printer-other-brand','ops-other','ops-branch-other','TEST other brand','TEST','other-brand-token',now(),'ops-other')")
        for n,(key,city) in enumerate(cities.items(),2):
            c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,'ops-brand',%s,%s,'{}')",('city-'+key,n,'city-source-'+key,key,city))
            c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,printer_name,token_hash,approved_at,created_by) VALUES(%s,'ops-brand',%s,%s,'TEST',%s,now(),'ops-manager')",('printer-'+key,'city-'+key,key,'synthetic-token-'+key))
        c.execute("UPDATE users SET branch_ids=%s WHERE id='ops-manager'",(db.json(['ops-branch']+['city-'+k for k in cities]),))
    response=actor('manager').get('/api/printing/operational-devices',params={'order_id':oid})
    assert response.status_code==200,response.text
    assert {r['id'] for r in response.json()['devices']}=={'printer-same'}
    # Global administration still lists every authorized branch printer.
    assert {r['id'] for r in actor('manager').get('/api/printing/devices').json()['devices']} == {'printer-same','printer-khafji','printer-rafha'}
    assert len(actor('owner').get('/api/printing/devices').json()['devices'])==3
    for role in ('manager','owner'):
        post(role,'/api/printing/orders/'+oid,{'device_id':'printer-other-brand'},status=409)
        for key in ('khafji','rafha'):
            result=post(role,'/api/printing/orders/'+oid,{'device_id':'printer-'+key},status=403)
            assert result['detail']['code']=='PRINT_CITY_MISMATCH'
    post('manager','/api/printing/orders/'+oid,{'device_id':'printer-same'})
    with db.tx() as c:
        c.execute("UPDATE users SET branch_ids='[\"ops-branch\"]' WHERE id='ops-manager'")
    assert actor('manager').get('/api/printing/operational-devices',params={'order_id':oid}).json()['devices']==[]
    assert actor('manager').get('/api/printing/devices').json()['devices']==[]
    post('manager','/api/printing/orders/'+oid,{'device_id':'printer-same','reprint_reason':'TEST scope revoked'},status=404)
    with db.tx() as c:
        rows=c.execute('SELECT device_id FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchall()
        assert [r['device_id'] for r in rows]==['printer-same']
