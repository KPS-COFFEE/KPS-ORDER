"""Configuration acceptance tests through real authorized sessions and PostgreSQL."""
import io
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from app import admin,auth,db


def test_configuration_rejects_malformed_options_and_city_aliases():
    assert admin.city('رفحاء')=='رفحاء'
    assert admin.city('رفحا')=='رفحاء'
    assert admin.city('حفر الباطن')=='حفرالباطن'
    for groups in [[42],[{'code':'COLOR','values':[42]}],[{'code':'COLOR','values':[{'code':'BLK'}],'min_select':False}]]:
        with pytest.raises(HTTPException):admin.validate_options(groups)
    with pytest.raises(HTTPException):
        admin.validate_branch_settings({'delivery_enabled':True},'الدمام')
    with pytest.raises(HTTPException):
        admin.validate_branch_settings({'coverage':{'type':'radius','center':{'lat':True,'lng':45},'radius_meters':10}},'حفر الباطن')


@pytest.fixture
def owner(client):
    password='TestOnly!PrivatePassword1'
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,username,display_name,role,brand_id,password_hash) VALUES('admin-test-owner','admin-test-owner','اختبار الإدارة','owner','kps',%s)",(auth.hash_password(password),))
    response=client.post('/api/auth/login',json={'username':'admin-test-owner','password':password})
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    def mutate(method,path,body=None,key=None,files=None):
        headers={'Idempotency-Key':key or uuid4().hex}
        return client.request(method,path,json=body,files=files,headers=headers)
    return client,mutate


@pytest.mark.postgres
def test_original_ids_and_branch_numbers_survive_admin_changes_and_restart(owner):
    client,mutate=owner
    before=client.get('/api/admin/config').json()
    assert len(before['branches'])==20
    changed=mutate('PATCH','/api/admin/branches/kps-branch-1',{'name':'اسم اختبار','settings':{'pickup_enabled':False}})
    assert changed.status_code==200,changed.text
    assert mutate('PATCH','/api/admin/branches/kps-branch-1',{'branch_no':99}).status_code==422
    assert mutate('POST','/api/admin/branches',{'branch_no':1,'name':'مكرر','city':'حفر الباطن'}).status_code==409
    db.migrate()
    after=client.get('/api/admin/config').json()
    assert len(after['branches'])==20
    row=next(x for x in after['branches'] if x['branch_no']==1)
    assert row['id']=='kps-branch-1' and row['name']=='اسم اختبار'
    assert row['source_id']==next(x for x in before['branches'] if x['branch_no']==1)['source_id']


@pytest.mark.postgres
def test_cities_and_branch_availability_policy_are_admin_authorized_not_url_overrides(owner):
    client,mutate=owner
    r=mutate('PATCH','/api/admin/branches/kps-branch-1',{'city':'الدمام','settings':{'delivery_enabled':True}})
    assert r.status_code==422 and r.json()['detail']['code']=='DELIVERY_CITY_FORBIDDEN'
    r=mutate('PATCH','/api/admin/products/kps-33',{'settings':{'available_cities':['رفحاء']}})
    assert r.status_code==200,r.text
    assert r.json()['settings']['availability_policy_version']==1
    with db.tx() as conn:
        row=conn.execute("SELECT * FROM branches WHERE city='رفحاء' LIMIT 1").fetchone()
    assert client.get('/api/products/kps-33/availability',params={'branch_id':row['id']}).json()['available'] is True
    r=mutate('PATCH','/api/admin/products/kps-33',{'settings':{'unavailable_branch_ids':[row['id']]}})
    assert r.status_code==200,r.text
    assert client.get('/api/products/kps-33/availability',params={'branch_id':row['id']}).json()['available'] is False
    assert mutate('PATCH','/api/admin/products/kps-33',{'settings':{'option_groups':[42]}}).status_code==422


@pytest.mark.postgres
def test_real_banner_upload_schedule_scope_edit_persistence_and_private_preview(owner):
    client,mutate=owner
    image=io.BytesIO();Image.new('RGB',(50,20),'orange').save(image,format='PNG')
    uploaded=mutate('POST','/api/admin/assets',files={'file':('TEST.png',image.getvalue(),'image/png')})
    assert uploaded.status_code==200,uploaded.text
    aid=uploaded.json()['id']
    payload={'title':'إعلان اختبار موسوم','body':'اختبار فقط دون خصم','desktop_asset_id':aid,'active':True,'branch_ids':['kps-branch-1'],'destination':{'kind':'product','id':'kps-33'}}
    created=mutate('POST','/api/admin/banners',payload,key='test-banner-create')
    assert created.status_code==200,created.text
    assert mutate('POST','/api/admin/banners',payload,key='test-banner-create').json()==created.json()
    bid=created.json()['id']
    assert client.get('/api/banners').json()['banners']==[]
    visible=client.get('/api/banners?branch_id=kps-branch-1').json()['banners']
    assert len(visible)==1 and visible[0]['destination']['id']=='kps-33'
    data=client.get(visible[0]['desktop_image_url']);assert data.status_code==200 and data.headers['content-type']=='image/jpeg'
    with db.tx() as conn: count=conn.execute('SELECT count(*) n FROM assets WHERE id=%s',(aid,)).fetchone()['n']
    assert count==1
    db.migrate()
    assert len(client.get('/api/banners?branch_id=kps-branch-1').json()['banners'])==1
    assert mutate('PATCH','/api/admin/banners/'+bid,{'starts_at':'2099-01-01T00:00:00+03:00'}).status_code==200
    assert client.get('/api/banners?branch_id=kps-branch-1').json()['banners']==[]
    assert client.get(visible[0]['desktop_image_url']).status_code==404
    assert mutate('PATCH','/api/admin/banners/'+bid,{'destination':{'kind':'html','id':'javascript:alert(1)'}}).status_code==422
    client.cookies.clear()
    assert client.get('/api/admin/assets/'+aid).status_code==401


@pytest.mark.postgres
def test_employee_creation_scope_session_revocation_and_owner_protection(owner):
    client,mutate=owner
    assert mutate('POST','/api/admin/users',{'username':'test-driver','role':'driver','password':'TestOnly!DriverPassword1'}).status_code==422
    created=mutate('POST','/api/admin/users',{'username':'test-driver','role':'driver','password':'TestOnly!DriverPassword1','branch_ids':['kps-branch-1']})
    assert created.status_code==200,created.text
    assert 'password_hash' not in created.json()
    uid=created.json()['id']
    owner_cookies=dict(client.cookies);owner_csrf=client.headers['X-CSRF-Token']
    login=client.post('/api/auth/login',json={'username':'test-driver','password':'TestOnly!DriverPassword1'});assert login.status_code==200
    employee_cookies=dict(client.cookies)
    client.cookies.clear();client.cookies.update(owner_cookies);client.headers['X-CSRF-Token']=owner_csrf
    assert mutate('PATCH','/api/admin/users/'+uid,{'active':False}).status_code==200
    assert mutate('PATCH','/api/admin/users/admin-test-owner',{'active':False}).status_code==409
    client.cookies.clear();client.cookies.update(employee_cookies)
    assert client.get('/api/session').json()['user'] is None


@pytest.mark.postgres
def test_add_brand_same_database_creates_separate_scoped_owner_and_no_catalog_fork(owner):
    client,mutate=owner
    payload={'id':'testbrand','name':'براند اختبار','owner_username':'testbrand-owner','owner_password':'TestOnly!BrandPassword1'}
    r=mutate('POST','/api/admin/brands',payload,key='create-second-brand');assert r.status_code==200,r.text
    assert mutate('POST','/api/admin/brands',payload,key='create-second-brand').json()==r.json()
    assert client.get('/api/catalog?brand_id=testbrand').json()['items']==[]
    own=client.get('/api/admin/config').json();assert own['brand']['id']=='kps'
    client.cookies.clear()
    login=client.post('/api/auth/login',json={'username':'testbrand-owner','password':'TestOnly!BrandPassword1'});assert login.status_code==200
    client.headers['X-CSRF-Token']=login.json()['csrf_token']
    assert client.get('/api/admin/config').json()['brand']['id']=='testbrand'
    assert mutate('PATCH','/api/admin/products/kps-33',{'price_halalas':1}).status_code==404
