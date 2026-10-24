"""Native ring delivery: owner scope, strict audio data, immutable digest URLs."""
import base64
import hashlib
import io
import struct
import wave

import pytest
from app import db
from app.driver_order_sounds import approved_wave, valid_wave
from tests.test_core import _make_user
from tests.sms_policy_helpers import sms_login


def replacement():
    output=io.BytesIO()
    with wave.open(output,'wb') as f:
        f.setparams((1,2,22050,0,'NONE','not compressed'))
        f.writeframes(struct.pack('<h',1000)*22050)
    return output.getvalue()


def test_approved_waveform_is_exact_owner_selected_sample():
    data=approved_wave()
    assert valid_wave(data)
    assert hashlib.sha256(data).hexdigest()=='e31f18ed4c2a3ab8acf0950adebc50dade1046e94f70d9eeb1d86e7196dc577c'
    assert len(data)==793844


@pytest.mark.parametrize('kind',['header','size','format','stereo','bits','metadata','rate','alignment','too_short','too_long'])
def test_invalid_audio_rejected(kind):
    data=bytearray(replacement())
    if kind=='header':data[:4]=b'PKxx'
    elif kind=='size':struct.pack_into('<I',data,4,1)
    elif kind=='format':struct.pack_into('<H',data,20,3)
    elif kind=='stereo':struct.pack_into('<H',data,22,2)
    elif kind=='bits':struct.pack_into('<H',data,34,8)
    elif kind=='metadata':data[36:40]=b'LIST'
    elif kind=='rate':struct.pack_into('<I',data,24,1)
    elif kind=='alignment':struct.pack_into('<H',data,32,4)
    elif kind=='too_short':
        data=data[:46];struct.pack_into('<I',data,4,38);struct.pack_into('<I',data,40,2)
    elif kind=='too_long':data=bytearray(1000001)
    assert not valid_wave(data)


@pytest.mark.postgres
def test_owner_replace_preview_idempotency_reset_and_audit(client,monkeypatch):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch)
    original=client.get('/api/operations/driver-order-sound')
    assert original.status_code==200 and original.json()['sound']['default']
    default=original.json()['sound']
    download=client.get(default['path'])
    assert download.content==approved_wave() and download.headers['content-type']=='audio/wav'
    assert 'no-store' in download.headers['cache-control']
    data=replacement();body={'name':'Synthetic ring','data_base64':base64.b64encode(data).decode()}
    headers={'Idempotency-Key':'sound-replace-test'}
    result=client.patch('/api/admin/driver-order-sound',json=body,headers=headers)
    assert result.status_code==200,result.text
    assert client.patch('/api/admin/driver-order-sound',json=body,headers=headers).json()==result.json()
    meta=client.get('/api/operations/driver-order-sound').json()['sound']
    assert meta['sha256']==hashlib.sha256(data).hexdigest() and not meta['default']
    assert client.get(meta['path']).content==data
    assert client.get(default['path']).status_code==404
    assert client.patch('/api/admin/driver-order-sound',json={'reset':True},headers={'Idempotency-Key':'sound-reset-test'}).status_code==200
    assert client.get('/api/operations/driver-order-sound').json()['sound']['sha256']==default['sha256']
    with db.tx() as c:
        events=c.execute("SELECT details FROM audit_events WHERE action='driver.sound.updated' AND actor_id=%s",(owner['id'],)).fetchall()
        assert len(events)==2
        assert all('data_base64' not in str(row) for row in events)
        assert not c.execute('SELECT 1 FROM driver_order_audio WHERE brand_id=%s',(owner['brand_id'],)).fetchone()


@pytest.mark.postgres
@pytest.mark.parametrize('role',['admin','delivery_manager','delivery_staff','branch_manager','driver'])
def test_staff_can_read_but_cannot_change(client,monkeypatch,role):
    user=_make_user(role);sms_login(client,user,monkeypatch)
    assert client.get('/api/operations/driver-order-sound').status_code==200
    assert client.patch('/api/admin/driver-order-sound',json={'reset':True},headers={'Idempotency-Key':'sound-forbidden'}).status_code==403


@pytest.mark.postgres
def test_audio_cannot_be_read_anonymously_or_cross_brand(client,monkeypatch):
    assert client.get('/api/operations/driver-order-sound').status_code==401
    owner=_make_user('owner');sms_login(client,owner,monkeypatch)
    body={'name':'Brand one','data_base64':base64.b64encode(replacement()).decode()}
    sound=client.patch('/api/admin/driver-order-sound',json=body,headers={'Idempotency-Key':'brand-one-ring'}).json()['sound']
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('sound-other','Synthetic other')")
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('sound-other','{}')")
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED','true')
    other=_make_user('owner',brand='sound-other');sms_login(client,other,monkeypatch)
    assert client.get(sound['path']).status_code==404
    assert client.get('/api/operations/driver-order-sound').json()['sound']['default']


@pytest.mark.postgres
@pytest.mark.parametrize('body',[{'reset':1},{'reset':True,'brand_id':'other'},{'url':'https://evil.invalid'},
    {'name':'Bad','data_base64':'%%%%'},{'name':'Bad','data_base64':base64.b64encode(b'not audio').decode()},
    {'name':'x'*81,'data_base64':'AAAA'},{'name':'Unsafe\nname','data_base64':'AAAA'}])
def test_invalid_requests_do_not_mutate(client,monkeypatch,body):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch)
    response=client.patch('/api/admin/driver-order-sound',json=body,headers={'Idempotency-Key':'invalid-sound'})
    assert response.status_code==422,response.text
    assert client.get('/api/operations/driver-order-sound').json()['sound']['default']
