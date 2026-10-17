"""Public response contract for the owner-approved, byte-exact link preview."""
import hashlib
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.main import app


class Metadata(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.values = {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and 'property' in attrs:
            key = attrs['property']
            assert key not in self.values, key
            self.values[key] = attrs.get('content')


def test_server_html_has_one_approved_preview_without_body_ad():
    response = TestClient(app).get('/')
    assert response.status_code == 200
    head, body = response.text.split('</head>', 1)
    meta = Metadata(head).values
    assert meta['og:title'] == 'KPS COFFEE | كبز للقهوة المختصة'
    assert meta['og:description'] == 'قهوتك صارت أقرب… اطلبها بكل سهولة ☕🧡'
    assert meta['og:url'] == 'https://order.kps.sa'
    assert meta['og:image'] == 'https://order.kps.sa/assets/kps-whatsapp-cookie-2e1553c18c2e.jpg'
    assert meta['og:image:secure_url'] == meta['og:image']
    assert meta['og:image:type'] == 'image/jpeg'
    assert 'kps-whatsapp-' not in body
    assert 'cf8b1309' not in head


@pytest.mark.parametrize('name,mime,digest', [
    ('kps-whatsapp-cookie-2e1553c18c2e.jpg', 'image/jpeg', '2e1553c18c2e94174d7a8ce4e7f1d05ac0471d99ee2a1982aa4761c7906407a5'),
    ('kps-whatsapp-cf8b1309a87e.jpeg', 'image/jpeg', 'cf8b1309a87eea6fe219d26ea2dce98c25c017c714762af933241504a45e6384'),
    ('kps-whatsapp-cookie-815deea7ce4d.png', 'image/png', '815deea7ce4d56c203874f2d666086c776e2e6734290c4d7a472e333b263e1ae'),
])
def test_public_image_bytes_and_media_type(name, mime, digest):
    response = TestClient(app).get('/assets/' + name)
    assert response.status_code == 200
    assert response.headers['content-type'] == mime
    assert hashlib.sha256(response.content).hexdigest() == digest
    if name.endswith('.jpg'):
        assert len(response.content) < 280000
    from PIL import Image
    from io import BytesIO
    image = Image.open(BytesIO(response.content))
    image.verify()
    if mime == 'image/jpeg':
        assert image.size == (1536, 1024)


@pytest.mark.parametrize('query', ['brand_id=enbishan', 'brand_id=', 'brand_id=kps&brand_id=enbishan'])
def test_other_brand_html_does_not_leak_kps_preview(query):
    response = TestClient(app).get('/?' + query)
    assert response.status_code == 200
    assert not Metadata(response.text).values
    assert 'kps-whatsapp-' not in response.text
