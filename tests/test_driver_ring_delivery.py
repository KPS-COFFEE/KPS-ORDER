"""Exercise the same descriptor and download sequence as the native driver."""
import hashlib

from app import db
from test_enbishan_dual_brand import dual


def test_native_ring_delivery_and_membership_revocation(dual):
    _, actor, _, _, _ = dual
    driver = actor('driver')
    endpoint = '/api/operations/driver-order-sound'
    for brand, digest in {
        'kps': 'e31f18ed4c2a3ab8acf0950adebc50dade1046e94f70d9eeb1d86e7196dc577c',
        'enbishan': '1c7d03a750534c1b48cab17d36d596049d243d226410784d8f1a15fa3c8337e5',
    }.items():
        descriptor = driver.get(endpoint, params={'brand_id': brand}).json()['sound']
        assert descriptor['brand_id'] == brand and descriptor['sha256'] == digest
        audio = driver.get(descriptor['path'])
        assert audio.headers['content-type'] == 'audio/wav'
        assert 'no-store' in audio.headers['cache-control']
        assert len(audio.content) == descriptor['bytes'] == 793844
        assert hashlib.sha256(audio.content).hexdigest() == digest

    with db.tx() as conn:
        conn.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    assert driver.get(descriptor['path']).status_code == 404
    assert driver.get(endpoint, params={'brand_id': 'enbishan'}).status_code == 404
    driver.cookies.clear()
    assert driver.get(endpoint).status_code == 401


def test_previous_custom_ring_is_delivered_when_present(dual):
    from app.driver_order_sounds import approved_wave

    _, actor, _, _, _ = dual
    custom = bytearray(approved_wave('kps'))
    custom[100:102] = b'AB'
    digest = hashlib.sha256(custom).hexdigest()
    with db.tx() as conn:
        conn.execute('CREATE TABLE driver_order_audio (brand_id text PRIMARY KEY, name text, sha256 text, audio bytea)')
        conn.execute('INSERT INTO driver_order_audio VALUES (%s,%s,%s,%s)', ('kps', 'Saved ring', digest, bytes(custom)))
    driver = actor('driver')
    sound = driver.get('/api/operations/driver-order-sound?brand_id=kps').json()['sound']
    assert sound['default'] is False and sound['sha256'] == digest
    assert driver.get(sound['path']).content == bytes(custom)


def test_favicon_uses_storefront_brand(client, monkeypatch):
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED', 'true')
    for host, asset in [('kps.test', 'kps-favicon.ico'), ('enbishan.test', 'enbishan-favicon.ico')]:
        monkeypatch.setenv('PUBLIC_BASE_URL', 'https://' + host)
        icon = client.get('/favicon.ico')
        assert icon.status_code == 200
        assert icon.content == client.get('/assets/' + asset).content
        assert icon.headers['content-type'].startswith('image/')
        assert client.get('/favicon.ico', headers={'If-None-Match': icon.headers['etag']}).status_code == 304
