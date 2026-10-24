"""Real browser + PG18; only outbound SMS is synthetic."""
import os
import socket
import threading
import time
import pytest
import uvicorn
import httpx
from fastapi.testclient import TestClient
from app import auth,db
from tests.test_core import _authenticate,_make_user
from tests.sms_policy_helpers import configure_sms
from tests.test_owner_phone_browser import chromium_browser
from test_browser import _context

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated PostgreSQL and actual Chromium')]


@pytest.fixture
def pilot_app(database,monkeypatch):
    configure_sms(monkeypatch)
    owner=_make_user('owner',username='synthetic_pilot_owner',password='Synthetic-Pilot-Only-42')
    from app.main import app
    with TestClient(app) as client:token,csrf=_authenticate(client,owner)
    with db.tx() as conn:
        row=conn.execute("UPDATE users SET phone='+966507778899',phone_sms_verified_at=now(),phone_verified_at=now() WHERE id=%s RETURNING *",(owner['id'],)).fetchone()
        sid=conn.execute('SELECT id FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()['id']
        conn.execute("INSERT INTO owner_phone_challenges(id,user_id,session_id,brand_id,phone,account_updated_at,provider,provider_reference,expires_at,verified_at) VALUES('owner_phone_fixture',%s,%s,'kps',%s,%s,'tawked','synthetic-binding',now()+interval '5 minutes',now())",(owner['id'],sid,row['phone'],row['updated_at']))
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    monkeypatch.setenv('PUBLIC_BASE_URL',base);monkeypatch.setenv('APP_ALLOWED_ORIGINS',base)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            try:
                if httpx.get(base+'/ready',timeout=1,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.1)
        else:pytest.fail('Local pilot app not ready')
        yield base,owner,token,csrf
    finally:server.should_exit=True;thread.join(timeout=10);assert not thread.is_alive()


@pytest.mark.parametrize('lang',['ar','en'])
def test_actual_app_pilot_activation_and_personal_shopping_share_browser(pilot_app,chromium_browser,lang):
    from playwright.sync_api import expect
    base,owner,token,csrf=pilot_app
    context,page,errors=_context(chromium_browser,base)
    try:
        context.add_cookies([{'name':auth.COOKIE,'value':token,'url':base},{'name':auth.CSRF_COOKIE,'value':csrf,'url':base}])
        page.goto(base+'/sms-login-test?lang='+lang,wait_until='networkidle')
        # Mandatory SMS retirement also applies before starting the pilot.
        # Password verification must fail; obtain a genuine session-bound SMS
        # proof through the unchanged API, then exercise the pilot's own UI.
        work_headers={'Origin':base,'X-KPS-Context':'work','X-CSRF-Token':csrf}
        rejected=context.request.post(base+'/api/identity/step-up',headers=work_headers,
                                      data={'password':'Synthetic-Pilot-Only-42'})
        assert rejected.status==403 and rejected.json()['detail']['code']=='WORK_SMS_REQUIRED'
        sent_proof=context.request.post(base+'/api/identity/owner/sms/request',headers=work_headers,data={})
        assert sent_proof.ok,sent_proof.text()
        challenge=sent_proof.json()['challenge_id']
        wrong=context.request.post(base+'/api/identity/owner/sms/verify',headers=work_headers,
                                   data={'challenge_id':challenge,'code':'9999'})
        assert wrong.status==400,wrong.text()
        proof=context.request.post(base+'/api/identity/owner/sms/verify',headers=work_headers,
                                   data={'challenge_id':challenge,'code':'0123'})
        assert proof.ok,proof.text()
        with db.tx() as conn:
            verified=conn.execute('SELECT source_user_id,source_session_id,verified_at FROM otp_challenges WHERE id=%s',(challenge,)).fetchone()
            sid=conn.execute('SELECT id FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()['id']
            assert verified['source_user_id']==owner['id'] and verified['source_session_id']==sid
            assert verified['verified_at'] is not None
        page.locator('input[name=password]').fill('')
        page.locator('#start').click();expect(page.locator('#login')).to_be_visible()
        page.locator('#login input[name=code]').fill('٠١٢٣');page.locator('#verify-login').click()
        expect(page.locator('#sensitive')).to_be_visible()
        page.locator('#send-sensitive').click();expect(page.locator('#activate')).to_be_visible()
        page.locator('#activate input[name=code]').fill('0123');page.locator('#verify-activate').click()
        expect(page.locator('#activate')).not_to_be_visible()
        assert 'SMS' in page.locator('#status').inner_text()
        headers={'Origin':base,'X-KPS-Context':'customer'}
        sent=context.request.post(base+'/api/auth/otp/request',headers=headers,data={'phone':'0507778899'})
        assert sent.ok,sent.text()
        bought=context.request.post(base+'/api/auth/otp/verify',headers=headers,data={'challenge_id':sent.json()['challenge_id'],'code':'0123','display_name':'Synthetic buyer'})
        assert bought.ok,bought.text()
        assert bought.json()['user']['role']=='customer' and bought.json()['user']['id']!=owner['id']
        work=context.request.get(base+'/api/session',headers={'X-KPS-Context':'work'}).json()
        assert work['user']['id']==owner['id'] and work['user']['role']=='owner'
        out=context.request.post(base+'/api/auth/logout',headers={**headers,'X-CSRF-Token':bought.json()['csrf_token']},data={})
        assert out.ok,out.text()
        assert context.request.get(base+'/api/session',headers={'X-KPS-Context':'work'}).json()['user']['id']==owner['id']
        with db.tx() as conn:
            policy=conn.execute("SELECT settings->'employee_sms_rollout' p FROM app_settings WHERE brand_id='kps'").fetchone()['p']
            assert policy['mode']=='active' and policy['email_retired']
            assert conn.execute('SELECT email_verified_at FROM users WHERE id=%s',(owner['id'],)).fetchone()['email_verified_at'] is None
        assert not errors,errors
    finally:context.close()
