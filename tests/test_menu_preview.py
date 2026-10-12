"""Private menu acceptance against canonical administration and isolated PostgreSQL."""
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4
import secrets
import pytest
from fastapi import HTTPException
from app import auth,db,catalog,menu_preview,customer_information
from test_admin import owner
from test_final_catalog import _prepare_branch

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('url',['javascript:alert(1)','https://instagram.com.evil.invalid/kps','https://user@instagram.com/kps','http://g.page/r/id/review','https://instagram.com:444/kps','https://example.invalid/'])
def test_external_links_fail_closed(url):
    assert customer_information.safe_link(url) is None
    assert customer_information.safe_link(url,'review') is None


def test_rating_requires_source_timestamp_and_profile_identity():
    review='https://g.page/r/TEST_ONLY_BRANCH/review'
    valid={'value':4.6,'review_count':10,'as_of':'2026-01-01T00:00:00+00:00','profile_url':review,'source':'approved_cached'}
    assert customer_information.validate_information({'google_rating':valid})
    for bad in [valid|{'value':float('nan')},valid|{'value':True},valid|{'review_count':-1},valid|{'source':'live'},valid|{'as_of':'2099-01-01T00:00:00Z'}]:
        with pytest.raises(HTTPException):customer_information.validate_information({'google_rating':bad})
    branch={'settings':{'customer_information':{'google_rating':valid,'review_url':review}}}
    assert customer_information.public_information(branch)['google_rating']==valid
    branch['settings']['customer_information']['review_url']='https://g.page/r/ANOTHER_BRANCH/review'
    assert customer_information.public_information(branch)['google_rating'] is None
    assert customer_information.public_information({'settings':{}})['google_rating'] is None


def test_preview_source_is_lightweight_and_has_no_gps_branch_picker_or_public_activation():
    html=(ROOT/'public/menu-preview.html').read_text();js=(ROOT/'public/menu-preview.js').read_text()
    assert 'customer.js' not in html and 'ops.js' not in html
    assert 'geolocation' not in js and 'getCurrentPosition' not in js
    assert 'branch-select' not in html and 'branch-select' not in js
    assert len(js.encode())<16000
    assert '/menu/handoff' in html
    assert 'location' not in __import__('re').findall(r'navigator\.([a-zA-Z]+)',js)


@pytest.mark.parametrize('period',['today','7d','30d'])
def test_riyadh_periods(period):
    a,b=menu_preview.period_range(period,None,None)
    assert a.utcoffset().total_seconds()==10800
    assert (b-a).days=={'today':1,'7d':7,'30d':30}[period]


@pytest.mark.postgres
def test_preview_auth_assets_csrf_revocation_and_no_public_route(client):
    for url in ['/menu/preview','/menu/assets/menu-preview.js','/api/menu/preview','/menu/analytics','/api/menu/analytics']:
        assert client.get(url).status_code==401
    with db.tx() as c:
        c.execute("INSERT INTO users(id,role,brand_id,permissions,branch_ids) VALUES('menu-limited','admin','kps','[]','[]')")
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions(id,user_id,token_hash,csrf_hash,expires_at) VALUES('menu-limited-session','menu-limited',%s,%s,now()+interval '1 hour')",(auth.token_hash(token),auth.token_hash(csrf)))
    client.cookies.set(auth.COOKIE,token);client.headers['X-CSRF-Token']=csrf
    assert client.get('/menu/preview').status_code==403
    with db.tx() as c:c.execute("UPDATE users SET permissions='[\"products_manage\"]' WHERE id='menu-limited'")
    r=client.get('/menu/preview');assert r.status_code==200
    assert r.headers['Cache-Control']=='private, no-store'
    assert 'geolocation=()' in r.headers['Permissions-Policy']
    assert 'noindex' in r.headers['X-Robots-Tag']
    assert client.get('/menu/analytics').status_code==403
    assert client.get('/api/menu/analytics').status_code==403
    client.get('/api/menu/preview')
    assert client.post('/api/menu/events',json={'id':uuid4().hex,'event_type':'visit'},headers={'X-CSRF-Token':'wrong'}).status_code==403
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='menu-limited'")
    assert client.get('/api/menu/preview').status_code==401
    assert client.get('/menu-preview.html').status_code==404
    assert client.get('/menu-preview.js').status_code==404
    assert client.get('/').status_code==200


@pytest.mark.postgres
def test_catalog_prices_categories_options_global_and_branch_availability(owner):
    client,mutate=owner
    preview=client.get('/api/menu/preview').json();canonical=client.get('/api/catalog').json()
    assert preview['preview'] and preview['public_activation_enabled'] is False
    assert len(preview['products'])==34
    for p,q in zip(preview['products'],canonical['products']):
        assert {k:v for k,v in p.items() if k!='globally_available'}==q
    pid=preview['products'][0]['id']
    with db.tx() as c:_prepare_branch(c)
    assert mutate('PATCH','/api/admin/products/'+pid,{'settings':{'enabled':False}}).status_code==200
    p=next(p for p in client.get('/api/menu/preview').json()['products'] if p['id']==pid)
    assert not p['globally_available']
    body={'brand_id':'kps','branch_id':'kps-branch-1','service':'pickup','payment_method':'cash','customer':{'name':'TEST ONLY','phone':'0500001234'},'items':[{'product_id':pid,'quantity':1}]}
    assert client.post('/api/quote',json=body).status_code==409
    assert mutate('PATCH','/api/admin/products/'+pid,{'settings':{'enabled':True}}).status_code==200
    assert next(p for p in client.get('/api/menu/preview').json()['products'] if p['id']==pid)['globally_available']
    assert mutate('POST',f'/api/branches/kps-branch-1/products/{pid}/availability',{'available':False,'reason':'TEST ONLY branch stop'}).status_code==200
    assert next(p for p in client.get('/api/menu/preview').json()['products'] if p['id']==pid)['globally_available']
    assert not next(p for p in client.get('/api/catalog?branch_id=kps-branch-1').json()['products'] if p['id']==pid)['available']
    assert client.post('/api/quote',json=body).status_code==409
    assert mutate('POST',f'/api/branches/kps-branch-1/products/{pid}/availability',{'available':True,'reason':'TEST ONLY restored'}).status_code==200


@pytest.mark.postgres
def test_new_product_single_source_and_no_stale_catalog_cache(owner):
    client,mutate=owner
    r=mutate('POST','/api/admin/products',{'name':'TEST ONLY new menu drink','category':'TEST ONLY','price_halalas':1234,'settings':{'name_en':'Test drink','category_en':'Test category'}})
    assert r.status_code==200,r.text
    pid=r.json()['product']['id']
    assert pid not in {p['id'] for p in client.get('/api/menu/preview').json()['products']}
    r=mutate('PATCH','/api/admin/products/'+pid,{'settings':{'lifecycle':'active','source_verified':True}})
    assert r.status_code==200,r.text
    for route in ['/api/menu/preview','/api/catalog']:
        r=client.get(route);p=next(p for p in r.json()['products'] if p['id']==pid)
        assert p['price_halalas']==1234 and p['name_en']=='Test drink'
        assert 'no-store' in r.headers['cache-control']
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM products WHERE id=%s',(pid,)).fetchone()['n']==1


@pytest.mark.postgres
def test_all_customer_information_preserved_20_unique_review_links_and_restart(owner):
    client,mutate=owner
    data=client.get('/api/menu/preview').json();branches=data['branches']
    assert len(branches)==20
    assert len({b['review_url'] for b in branches})==20
    assert all(b['review_url'].startswith('https://g.page/r/') and b['google_rating'] is None for b in branches)
    assert len(data['customer_links'])==5
    assert {l['url'] for l in data['customer_links']}=={'https://maps.app.goo.gl/Ctsp3CZPenJaVfpk8?g_st=iw','https://wa.me/966550472662','https://instagram.com/kps.coffe','https://www.snapchat.com/add/Kps.cofee','https://www.tiktok.com/@Kps.cofee'}
    canonical=client.get('/api/branches').json()['branches']
    for p,q in zip(branches,canonical):
        assert p['name']==q['name'] and p['city']==q['city']
        assert p['maps_url'] and catalog.safe_map_url(p['maps_url'])
        assert q['location']['lat'] and q['location']['lng']
    assert branches[0]['maps_url']!=branches[8]['maps_url']
    review='https://g.page/r/TEST_ONLY_OWNER_EDIT/review'
    assert mutate('PATCH','/api/admin/branches/kps-branch-1',{'settings':{'customer_information':{'review_url':review,'opening_information':'TEST ONLY opening note'}}}).status_code==200
    db.migrate()
    after=client.get('/api/menu/preview').json()
    assert after['branches'][0]['review_url']==review
    assert after['branches'][0]['opening_information']=='TEST ONLY opening note'
    assert after['customer_links']==data['customer_links']
    assert after['branches'][0]['maps_url']==branches[0]['maps_url']


@pytest.mark.postgres
def test_attribution_events_dedupe_preview_separation_invalid_handoff_and_ranges(owner):
    client,mutate=owner
    client.get('/api/menu/preview')
    event={'id':uuid4().hex,'event_type':'visit'}
    assert client.post('/api/menu/events',json=event).status_code==200
    assert client.post('/api/menu/events',json=event).status_code==200
    bad={'id':uuid4().hex,'event_type':'product_view','target':'other-tenant-product'}
    assert client.post('/api/menu/events',json=bad).status_code==422
    assert client.post('/api/menu/events',json={'id':uuid4().hex,'event_type':'scan_branch','target':'kps-branch-1'}).status_code==422
    r=client.get('/menu/handoff',follow_redirects=False)
    assert r.status_code==303
    dest=r.headers['location'];q=parse_qs(urlsplit(dest).query)
    assert q['source']==['menu_qr'] and menu_preview.TOKEN.fullmatch(q['menu_ref'][0])
    assert not {'phone','name','branch_id'}&set(q)
    assert client.get(dest).status_code==200
    assert client.get(dest).status_code==200
    m=client.get('/api/menu/analytics').json()
    assert m['metrics']['visit']==1 and m['unique_sessions']==1
    assert m['metrics']['order_start']==1 and m['metrics']['order_click']==1
    assert client.get('/api/menu/analytics?preview=false').json()['metrics']=={}
    assert client.get('/api/menu/analytics?period=custom&start=2026-10-12&end=2026-10-11').status_code==422
    assert client.get('/api/menu/analytics?period=custom&start=bad&end=bad').status_code==422
    with db.tx() as c:c.execute("UPDATE menu_handoffs SET expires_at=now()-interval '1 second'")
    client.cookies.delete(menu_preview.ATTR_COOKIE)
    client.get(dest)
    assert menu_preview.ATTR_COOKIE not in client.cookies


@pytest.mark.postgres
def test_order_attribution_is_durable_brand_scoped_idempotent_and_truthful(owner):
    client,mutate=owner
    client.get('/api/menu/preview');client.post('/api/menu/events',json={'id':uuid4().hex,'event_type':'visit'})
    dest=client.get('/menu/handoff',follow_redirects=False).headers['location'];client.get(dest)
    from test_browser import _customer_session
    customer,token,csrf=_customer_session()
    client.cookies.set(auth.COOKIE,token);client.headers['X-CSRF-Token']=csrf
    with db.tx() as c:
        _prepare_branch(c)
        c.execute("UPDATE users SET identity_kind='email',email='test-menu@example.invalid',email_verified_at=now() WHERE id=%s",(customer,))
    body={'brand_id':'kps','branch_id':'kps-branch-1','service':'pickup','payment_method':'cash','customer':{'name':'TEST ONLY menu customer','phone':'0500001234'},'items':[{'product_id':'kps-01','quantity':1}]}
    headers={'Idempotency-Key':'test-menu-order-0001'}
    r=client.post('/api/orders',json=body,headers=headers);assert r.status_code==200,r.text
    oid=r.json()['order']['id']
    assert client.post('/api/orders',json=body,headers=headers).json()['order']['id']==oid
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM menu_order_attribution WHERE order_id=%s',(oid,)).fetchone()['n']==1
    client.cookies.clear()
    login=client.post('/api/auth/login',json={'username':'admin-test-owner','password':'TestOnly!PrivatePassword1'});assert login.status_code==200
    client.headers['X-CSRF-Token']=login.json()['csrf_token']
    total=r.json()['order']['total_halalas']
    collected=client.post(f'/api/orders/{oid}/collect',json={'method':'cash','amount_halalas':total,'tendered_halalas':total},headers={'Idempotency-Key':'menu-test-payment'})
    assert collected.status_code==200,collected.text
    for status in ('accepted','preparing','ready','completed'):
        version=client.get(f'/api/orders/{oid}').json()['order']['version']
        done=client.post(f'/api/orders/{oid}/preparation',json={'status':status,'version':version},headers={'Idempotency-Key':'menu-test-'+status})
        assert done.status_code==200,done.text
    m=client.get('/api/menu/analytics').json()
    assert m['completed_orders']==1 and m['gross_revenue_halalas']==r.json()['order']['total_halalas']
    assert m['visit_to_completed_order_rate']==1 and m['order_branches'][0]['created_order_completion_rate']==1
    assert m['top_ordered_products'][0]['product_id']=='kps-01'
    assert client.get('/api/menu/analytics?preview=false').json()['completed_orders']==0


@pytest.mark.postgres
def test_preview_brand_scope_admin_branch_scope_and_links_authorization(owner):
    client,mutate=owner
    before=client.get('/api/menu/preview').json()
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('menu-other-brand','TEST ONLY other brand')")
        c.execute("INSERT INTO app_settings(brand_id) VALUES('menu-other-brand')")
        c.execute("UPDATE users SET brand_id='menu-other-brand' WHERE id='admin-test-owner'")
    other=client.get('/api/menu/preview').json()
    assert other['products']==[] and other['branches']==[] and other['customer_links']==[]
    assert mutate('PATCH','/api/admin/branches/kps-branch-1',{'settings':{'customer_information':{'opening_information':'INVALID SCOPE'}}}).status_code==404
    assert client.get('/api/menu/analytics').json()['metrics']=={}
    with db.tx() as c:c.execute("UPDATE users SET brand_id='kps',role='admin',permissions='[\"products_manage\",\"branches_manage\"]',branch_ids='[\"kps-branch-1\"]' WHERE id='admin-test-owner'")
    assert mutate('PATCH','/api/admin/branches/kps-branch-2',{'settings':{'customer_information':{'opening_information':'INVALID SCOPE'}}}).status_code==404
    assert client.patch('/api/menu/customer-links',json={'links':[]}).status_code==403
    assert client.get('/api/menu/analytics').status_code==403
    assert client.get('/api/menu/preview').json()['customer_links']==before['customer_links']
