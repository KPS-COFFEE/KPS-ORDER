"""Actual owner controls over a real isolated HTTP/Chromium session."""
import os
import pytest
from app import db
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail
from tests.test_driver_order_sounds import replacement

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance required')]


@pytest.mark.parametrize('language',['ar','en'])
def test_owner_can_preview_upload_and_restore_ring(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    owner=_staff('owner');context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,owner)
    page.on('dialog',lambda dialog:dialog.accept())
    try:
        page.goto(live_app+'/ops')
        if language=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator('[data-action=alert-policy]').click()
        page.get_by_role('button',name='Driver app sound — preview and change' if language=='en' else 'صوت تطبيق السائق — معاينة وتغيير',exact=True).click()
        expect(page.locator('[data-preview-current]')).to_be_visible()
        with page.expect_response(lambda r:'/api/operations/driver-order-sound/brands/' in r.url and '/audio/' in r.url) as current:
            page.locator('[data-preview-current]').click()
        assert current.value.status==200
        page.locator('[name=sound_file]').set_input_files({'name':'Synthetic ring.wav','mimeType':'audio/wav','buffer':replacement()})
        expect(page.locator('[data-preview-new]')).to_be_enabled()
        page.locator('[data-preview-new]').click()
        page.locator('[name=sound_name]').fill('Synthetic approved test')
        with page.expect_response(lambda r:'/api/admin/driver-order-sound' in r.url and r.request.method=='PATCH') as saved:
            page.locator('#modal button[type=submit]').click()
        assert saved.value.status==200
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            assert c.execute("SELECT name FROM driver_order_audio WHERE brand_id='kps'").fetchone()['name']=='Synthetic approved test'
        page.locator('[data-action=alert-policy]').click()
        page.get_by_role('button',name='Driver app sound — preview and change' if language=='en' else 'صوت تطبيق السائق — معاينة وتغيير',exact=True).click()
        page.locator('[name=reset_sound]').check()
        with page.expect_response(lambda r:'/api/admin/driver-order-sound' in r.url and r.request.method=='PATCH') as reset:
            page.locator('#modal button[type=submit]').click()
        assert reset.value.status==200
        with db.tx() as c:
            assert not c.execute("SELECT 1 FROM driver_order_audio WHERE brand_id='kps'").fetchone()
        assert not errors
    finally:context.close()
