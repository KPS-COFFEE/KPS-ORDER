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
    assert admin.city('قبة')=='قبه'
    for groups in [[42],[{'code':'COLOR','values':[42]}],[{'code':'COLOR','values':[{'code':'BLK'}],'min_select':False}]]:
        with pytest.raises(HTTPException):admin.validate_options(groups)
    with pytest.raises(HTTPException):
        admin.validate_branch_settings({'delivery_enabled':True},'الدمام')
    with pytest.raises(HTTPException):
        admin.validate_branch_settings({'coverage':{'type':'radius','center':{'lat':True,'lng':45},'radius_meters':10}},'حفر الباطن')


@pytest.mark.postgres
def test_disabled_branch_can_clear_form_fields_without_retaining_old_values(owner):
    client,mutate=owner
    configured={'pickup_enabled':True,'ordering_verified':True,'payment_methods':['cash'],
                'opening_hours':[{'days':list(range(7)),'start':'00:00','end':'00:00'}],
                'location':{'lat':28,'lng':45},'location_verified':True,
                'map_url':'https://www.google.com/maps/dir/?api=1&destination=28,45','map_url_verified':True}
    assert mutate('PATCH','/api/admin/branches/kps-branch-1',{'settings':configured}).status_code==200
    assert mutate('PATCH','/api/admin/branches/kps-branch-1',{'settings':{'opening_hours':[]}}).status_code==422
    empty={'pickup_enabled':False,'car_enabled':False,'delivery_enabled':False,'opening_hours':[],
           'delivery_hours':[],'payment_methods':[],'coverage':None,'map_url':None,'location':None}
    result=mutate('PATCH','/api/admin/branches/kps-branch-1',{'settings':empty})
    assert result.status_code==200,result.text
    settings=result.json()['settings']
    assert settings['opening_hours']==[] and settings['payment_methods']==[]
    assert settings['coverage'] is None and settings['map_url'] is None and settings['location'] is None
    assert settings['map_url_verified'] is False and settings['location_verified'] is False
    branch=next(b for b in client.get('/api/branches').json()['branches'] if b['id']=='kps-branch-1')
    assert branch['directions_url'] is None


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
    # Final owner instruction preserves Khaliya's Hafar-only guard even across legacy admin overrides.
    assert client.get('/api/products/kps-33/availability',params={'branch_id':row['id']}).json()['available'] is False
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
def test_legacy_employee_session_revocation_and_owner_protection(owner):
    client,mutate=owner
    # V5 requires email invitation for newly enrolled staff. Existing active
    # employees retain private password access during their safe transition.
    rejected=mutate('POST','/api/admin/users',{'username':'test-driver','role':'driver','password':'TestOnly!DriverPassword1','branch_ids':['kps-branch-1']})
    assert rejected.status_code==422
    assert rejected.json()['detail']['code']=='STAFF_EMAIL_INVITATION_REQUIRED'
    uid='test-existing-driver'
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,username,display_name,role,brand_id,branch_ids,password_hash) VALUES(%s,'test-driver','موظف سابق للاختبار','driver','kps',%s,%s)",
                     (uid,db.json(['kps-branch-1']),auth.hash_password('TestOnly!DriverPassword1')))
    owner_cookies=dict(client.cookies);owner_csrf=client.headers['X-CSRF-Token']
    login=client.post('/api/auth/login',json={'username':'test-driver','password':'TestOnly!DriverPassword1'});assert login.status_code==200
    employee_cookies=dict(client.cookies)
    client.cookies.clear();client.cookies.update(owner_cookies);client.headers['X-CSRF-Token']=owner_csrf
    assert mutate('PATCH','/api/admin/users/'+uid,{'active':False}).status_code==200
    assert mutate('PATCH','/api/admin/users/admin-test-owner',{'active':False}).status_code==409
    client.cookies.clear();client.cookies.update(employee_cookies)
    assert client.get('/api/session').json()['user'] is None


@pytest.mark.postgres
def test_add_brand_same_database_invites_separate_scoped_owner_and_no_catalog_fork(owner,monkeypatch):
    import base64
    from urllib.parse import urlsplit,parse_qs
    from app import email_delivery
    client,mutate=owner
    monkeypatch.setenv('EMAIL_PROVIDER','resend')
    monkeypatch.setenv('EMAIL_FROM','sender@example.invalid')
    monkeypatch.setenv('RESEND_API_KEY','synthetic-never-dispatched')
    monkeypatch.setenv('MAIL_OUTBOX_KEY',base64.urlsafe_b64encode(b'B'*32).decode())
    assert client.post('/api/identity/step-up',json={'password':'TestOnly!PrivatePassword1'}).status_code==200
    payload={'id':'testbrand','name':'براند اختبار','owner_email':'owner@example.invalid','owner_display_name':'مالك اختبار'}
    r=mutate('POST','/api/admin/brands',payload,key='create-second-brand');assert r.status_code==200,r.text
    assert r.json()['invitation_status']=='pending_email'
    assert mutate('POST','/api/admin/brands',payload,key='create-second-brand').json()==r.json()
    empty_catalog=client.get('/api/catalog?brand_id=testbrand')
    assert empty_catalog.status_code==200,empty_catalog.text
    assert empty_catalog.json()['products']==[]
    assert client.get('/api/admin/config').json()['brand']['id']=='kps'
    with db.tx() as conn:
        invited=conn.execute('SELECT * FROM users WHERE id=%s',(r.json()['owner_user_id'],)).fetchone()
        assert invited['active'] is False and invited['email_verified_at'] is None
        row=conn.execute('SELECT * FROM email_outbox WHERE dedupe_key=%s',('identity:'+r.json()['challenge_id'],)).fetchone()
        params=parse_qs(urlsplit(email_delivery._payload(row)['text'].splitlines()[-1]).fragment)
    client.cookies.clear()
    verified=client.post('/api/identity/email/verify',json={'challenge_id':params['identity_challenge'][0],'token':params['identity_token'][0],
                                                       'display_name':'مالك اختبار','phone':'0500000000'})
    assert verified.status_code==200,verified.text
    assert verified.json()['user']['id']==invited['id']
    client.headers['X-CSRF-Token']=verified.json()['csrf_token']
    assert client.get('/api/admin/config').json()['brand']['id']=='testbrand'
    assert mutate('PATCH','/api/admin/products/kps-33',{'price_halalas':1}).status_code==404


@pytest.mark.parametrize('role', ['admin', 'delivery_manager', 'branch_manager', 'driver', 'customer'])
def test_receipt_occasion_setting_is_owner_only_even_with_delegated_appearance(role):
    with pytest.raises(HTTPException) as error:
        admin.update_settings({'receipt_occasion_text': 'Happy occasion'}, user={'role': role, 'permissions': ['appearance_manage']}, idem='occasion-permission')
    assert error.value.status_code == 403


@pytest.mark.postgres
def test_owner_can_save_and_disable_receipt_occasion(owner):
    client, mutate = owner
    phrase = 'كل عام وأنتم بخير\nكبز تجمعنا'
    saved = mutate('PATCH', '/api/admin/settings', {'receipt_occasion_text': phrase})
    assert saved.status_code == 200, saved.text
    assert saved.json()['settings']['receipt_occasion_text'] == phrase
    assert client.get('/api/admin/config').json()['settings']['receipt_occasion_text'] == phrase
    disabled = mutate('PATCH', '/api/admin/settings', {'receipt_occasion_text': ''})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()['settings']['receipt_occasion_text'] == ''


@pytest.mark.parametrize('role', ['admin','delivery_manager','branch_manager','driver','customer'])
def test_occasion_style_cannot_be_changed_by_non_owner(role):
    with pytest.raises(HTTPException) as error:
        admin.update_settings({'receipt_occasion_style': {'font':'Tahoma','size':24,'bold':True,'align':'center'}}, user={'role':role}, idem='style-only')
    assert error.value.status_code == 403


@pytest.mark.parametrize('style', [None, {}, {'font':'Arial','size':99,'bold':True,'align':'center'}, {'font':'Arial','size':18,'bold':'false','align':'center'}, {'font':'url(evil)','size':18,'bold':True,'align':'center'}])
def test_invalid_occasion_style_rejected_before_write(style):
    with pytest.raises(HTTPException) as error:
        admin.update_settings({'receipt_occasion_style':style}, user={'role':'owner'}, idem='bad-style')
    assert error.value.status_code == 422
