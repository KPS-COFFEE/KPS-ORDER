from pathlib import Path
import json
from app import enbishan_menu_assets as assets
from app.enbishan_catalog_import import prepare_rows


def test_all_bundled_assets_are_exact_and_untrusted_paths_are_rejected():
    historical={k:v for k,v in assets.MANIFEST.items() if not k.startswith(('owner-20261025','owner-20261026'))}
    assert len(historical)==54
    assert len(assets.MANIFEST)-len(historical)==29
    assert len([key for key in historical if not key.startswith('owner-review:20261022:')])==49
    assert {key.rsplit(':',1)[-1] for key in assets.MANIFEST if key.startswith('owner-review:20261022:')} == {'mojito-grape.jpeg','mojito-mahashi.jpeg','mojito-fattah.jpeg','ramadan-box.jpeg','crispy-small.jpeg'}
    assert all(assets.valid_bundled(v['path']) for v in assets.MANIFEST.values())
    for value in ['/assets/kps-logo-fb911a7b4bb4.png','/etc/passwd','/assets/enbishan-menu/../../secret','https://evil.test/a.jpg']:
        assert assets.resolve(value) is None
        assert not assets.valid_bundled(value)


def test_approved_import_binds_cleaned_images_and_preserves_drafts():
    manifest=json.loads((Path(__file__).resolve().parents[1]/'docs/enbishan/menu-review/reference-menu.json').read_text())
    result=prepare_rows(manifest['rows'])
    images={entry['image_url'] for p in result['products'] for entry in p['settings']['variant_images']}
    assert len(images)==49
    assert len([x for x in images if '/clean-' in x])==11
    assert all(p['settings']['enabled'] is False for p in result['products'])
    assert not result['ordering_enabled']


def test_modified_asset_does_not_resolve(monkeypatch):
    reference=next(iter(assets.MANIFEST))
    monkeypatch.setitem(assets.MANIFEST,reference,{**assets.MANIFEST[reference],'sha256':'0'*64})
    assert assets.resolve(reference) is None
