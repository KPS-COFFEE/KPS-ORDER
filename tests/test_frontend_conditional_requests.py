"""A browser revalidation must avoid retransmitting unchanged public bundles."""
from fastapi.testclient import TestClient

from app.main import app


def test_frontend_bundle_revalidates_and_changes_invalidate(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'PUBLIC', tmp_path)
    script = tmp_path / 'customer.js'
    script.write_text('window.example = 1;')
    # No lifespan/database is needed to serve a public frontend file.
    client = TestClient(app)
    first = client.get('/customer.js')
    assert first.status_code == 200
    assert first.text == 'window.example = 1;'
    etag = first.headers['etag']
    cached = client.get('/customer.js', headers={'If-None-Match': etag})
    assert cached.status_code == 304 and cached.content == b''
    assert cached.headers['etag'] == etag
    assert cached.headers['x-content-type-options'] == 'nosniff'
    by_date = client.get('/customer.js', headers={'If-Modified-Since': first.headers['last-modified']})
    assert by_date.status_code == 304
    script.write_text('window.example = 222;')
    changed = client.get('/customer.js', headers={'If-None-Match': etag})
    assert changed.status_code == 200
    assert changed.text == 'window.example = 222;'
    assert changed.headers['etag'] != etag


def test_public_bundle_allowlist_and_symlinks_stay_closed(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'PUBLIC', tmp_path)
    (tmp_path / 'secret.txt').write_text('not public')
    (tmp_path / 'customer.js').symlink_to(tmp_path / 'secret.txt')
    client = TestClient(app)
    assert client.get('/secret.txt').status_code == 404
    assert client.get('/customer.js').status_code == 404
