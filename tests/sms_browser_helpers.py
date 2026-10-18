"""Real browser HTTP authentication; only the external SMS transport is synthetic."""
from .sms_policy_helpers import configure_sms,register_synthetic_mobile


def owner_sms_login(context,base,owner,monkeypatch,password):
    user=register_synthetic_mobile(owner)
    configure_sms(monkeypatch,user['brand_id'])
    headers={'Origin':base,'X-KPS-Context':'work'}
    denied=context.request.post(base+'/api/auth/login',headers=headers,
                                data={'username':owner['username'],'password':password})
    assert denied.status==403 and denied.json()['detail']['code']=='WORK_SMS_REQUIRED'
    sent=context.request.post(base+'/api/identity/staff/code/request',headers=headers,
                              data={'phone':user['phone'],'brand_id':user['brand_id']})
    assert sent.ok,sent.text()
    assert sent.json()['channel']=='sms' and sent.json()['code_length']==4
    verified=context.request.post(base+'/api/identity/staff/code/verify',headers=headers,
                                  data={'challenge_id':sent.json()['challenge_id'],'code':'0123'})
    assert verified.ok,verified.text()
    assert verified.json()['user']['id']==owner['id']
    assert verified.json()['user']['email_verified_at'] is None
    return {**headers,'X-CSRF-Token':verified.json()['csrf_token']}


def confirm_owner_sms(page):
    from playwright.sync_api import expect
    code=page.locator('dialog:not(#modal) input[name=code]')
    expect(code).to_be_visible()
    expect(code).to_have_attribute('maxlength','4')
    code.fill('0123')
    page.locator('dialog:not(#modal) button[type=submit]').click()
    expect(code).to_have_count(0)
