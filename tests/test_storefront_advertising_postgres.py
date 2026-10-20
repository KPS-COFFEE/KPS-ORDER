from uuid import uuid4
import pytest
from app import auth, db
from test_operations import ops

pytestmark=pytest.mark.postgres


def test_existing_content_hidden_until_owner_approval_and_preview_preserved(ops,monkeypatch):
    actor,_,_,_=ops
    monkeypatch.setattr(auth,'require_owner_step_up',lambda c,u:None)
    with db.tx() as c:
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand',%s),('ops-other','{}')",(db.json({'homepage_presentation':{'ticker_enabled':True,'ticker_lines':['عرض معتمد سابقًا'],'quote_enabled':True,'quote_text':'عبارة سابقة'},'first_order_gift':{'enabled':False}}),))
        c.execute("INSERT INTO banners(id,brand_id,title,active) VALUES('saved-banner','ops-brand','Saved banner',true)")
    path='/api/banners?brand_id=ops-brand'
    hidden=actor('owner').get(path).json()
    assert hidden['banners']==[]
    assert hidden['presentation']['ticker_enabled'] is False
    assert hidden['presentation']['quote_text']==''
    body={'advertising_enabled':True,'expected_version':0,'reason':'اعتماد ظهور الإعلانات'}
    assert actor('manager').put('/api/advertising/policy',json=body).status_code==403
    headers={'Idempotency-Key':uuid4().hex}
    enabled=actor('owner').put('/api/advertising/policy',json=body,headers=headers)
    assert enabled.status_code==200,enabled.text
    assert actor('owner').put('/api/advertising/policy',json=body,headers=headers).json()==enabled.json()
    visible=actor('owner').get(path).json()
    assert len(visible['banners'])==1
    assert visible['presentation']['ticker_enabled'] is True
    assert actor('other').get('/api/advertising/policy').json()['policy']['advertising_enabled'] is False
    body.update(advertising_enabled=False,expected_version=1)
    assert actor('owner').put('/api/advertising/policy',json=body,headers={'Idempotency-Key':uuid4().hex}).status_code==200
    assert actor('owner').get(path).json()['banners']==[]
    with db.tx() as c:
        assert c.execute("SELECT active FROM banners WHERE id='saved-banner'").fetchone()['active'] is True
        settings=c.execute("SELECT settings FROM app_settings WHERE brand_id='ops-brand'").fetchone()['settings']
        assert settings['homepage_presentation']['quote_text']=='عبارة سابقة'
        assert settings['first_order_gift']=={'enabled':False}
