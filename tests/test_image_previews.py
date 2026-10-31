import hashlib
import io
from pathlib import Path
import pytest
from PIL import Image
from fastapi import HTTPException
from app.image_previews import thumbnail,static_preview,ASSETS
from app import db
from test_admin import owner

@pytest.mark.postgres
def test_uploaded_preview_cache_and_private_admin_boundary(owner):
    client,mutate=owner
    upload=mutate('POST','/api/admin/assets?purpose=product',files={'file':('sample.jpg',(ASSETS/'kps-05-d9135faa0a84.jpg').read_bytes(),'image/jpeg')})
    assert upload.status_code==200,upload.text
    asset=upload.json()['id'];url='/api/product-assets/'+asset
    with db.tx() as c:
        c.execute("UPDATE products SET settings=settings||%s WHERE id='kps-01'",(db.json({'image_url':url,'enabled':True}),))
    original=client.get(url);preview=client.get(url+'?preview=true')
    assert original.status_code==preview.status_code==200
    assert len(preview.content)<len(original.content)
    assert preview.headers['cache-control']=='public, max-age=60'
    assert set(client.get(upload.json()['preview_url']).headers['cache-control'].split(', ')) == {'private', 'no-store'}
    with db.tx() as c:c.execute("UPDATE products SET settings=settings||'{\"enabled\":false}' WHERE id='kps-01'")
    assert client.get(url+'?preview=true').status_code==404

def test_display_derivative_preserves_original_and_aspect():
    path=ASSETS/'kps-05-d9135faa0a84.jpg';original=path.read_bytes()
    digest=hashlib.sha256(original).hexdigest();small=thumbnail(original)
    with Image.open(io.BytesIO(original)) as a,Image.open(io.BytesIO(small)) as b:
        assert b.format=='WEBP' and max(b.size)<=720
        assert abs(a.width/a.height-b.width/b.height)<.005
    assert len(small)<len(original)*.3
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    for invalid in ('../main.py','missing.jpg','kps.svg','x.jpg/..'):
        with pytest.raises(HTTPException):static_preview(invalid)

@pytest.mark.postgres
def test_image_cache_does_not_cache_private_api(client):
    preview=client.get('/media/previews/kps-05-d9135faa0a84.jpg')
    assert preview.status_code==200 and preview.headers['content-type']=='image/webp'
    assert 'max-age=86400' in preview.headers['cache-control']
    original=client.get('/assets/kps-05-d9135faa0a84.jpg')
    assert original.content==(ASSETS/'kps-05-d9135faa0a84.jpg').read_bytes()
    assert 'max-age=86400' in original.headers['cache-control']
    assert client.get('/api/auth/session').headers['cache-control']=='no-store'
    assert client.get('/api/product-assets/missing?preview=true').headers['cache-control']=='no-store'

from test_browser import _context,chromium_browser,live_app,synthetic_mail

@pytest.mark.postgres
def test_menu_preview_and_original_zoom(live_app,chromium_browser):
    from playwright.sync_api import expect
    context,page,errors=_context(chromium_browser,live_app)
    try:
        page.goto(live_app)
        images=page.locator('.product-card img');expect(images.first).to_be_visible()
        assert '/media/previews/' in images.first.get_attribute('src')
        assert images.first.get_attribute('loading')=='eager'
        page.locator('.product-content h3 button').first.click()
        expect(page.locator('#product-dialog')).to_be_visible()
        src=page.locator('#product-image').get_attribute('src')
        assert '/media/previews/' not in src and '?preview=true' not in src
        page.locator('#product-image-zoom').click()
        expect(page.locator('#image-full')).to_have_attribute('src',src)
        assert not errors
    finally:context.close()
