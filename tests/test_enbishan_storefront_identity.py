"""Approved brand presentation uses the shared customer page and policy text."""
from pathlib import Path
import pytest
from starlette.requests import Request
from app import storefront_scope, customer_policy_content, customer_legal

ROOT = Path(__file__).resolve().parents[1]


def page(brand):
    request = Request({'type': 'http', 'path': '/', 'query_string': ('brand_id='+brand).encode(), 'headers': []})
    return storefront_scope.customer_html((ROOT/'public/index.html').read_text(), request)


def test_kps_source_is_byte_identical_and_enbishan_has_no_kps_contacts():
    assert page('kps') == (ROOT/'public/index.html').read_text()
    text = page('enbishan')
    for stale in ['0550472662', '966550472662', 'kps.coffe', 'Kps.cofee', 'KPS COFFEE']:
        assert stale not in text
    for current in ['0509895855', '966509895855', 'instagram.com/Enbishan', 'snapchat.com/add/Enbishan', 'tiktok.com/@Enbishan', 'https://enbishan.com', '/assets/enbishan.css']:
        assert current in text
    assert 'public-contact-support' in text
    assert 'data-storefront-brand="enbishan"' in text


@pytest.mark.parametrize('kind', ['privacy','terms','refunds'])
@pytest.mark.parametrize('lang', ['ar','en'])
def test_enbishan_reuses_all_canonical_policy_sections_and_operator(kind,lang):
    rendered = customer_legal.page(kind,'enbishan',lang).body.decode()
    en = lang=='en'
    from html import escape
    for section in customer_policy_content.SECTIONS[kind]:
        expected = section[2+en].replace('KPS COFFEE','Enbishan').replace('كبز قهوة مختصة','عنبيشن')
        assert escape(expected) in rendered
    assert 'brand_id=enbishan' in rendered and 'brand_id=kps' not in rendered
    assert 'KPS COFFEE' not in rendered
    if kind == 'privacy':
        assert customer_policy_content.CONTROLLER in rendered
        assert customer_policy_content.CR in rendered
        assert '0509895855' in rendered
    assert customer_legal.page(kind,'kps',lang).body == customer_policy_content.render(kind,lang).body


def test_original_logo_and_cleaned_menu_assets_are_bundled():
    assert (ROOT/'public/assets/enbishan-original.jpeg').is_file()
    artwork_root = ROOT/'public/assets/enbishan-menu'
    images=[p for p in artwork_root.rglob('*') if p.is_file()]
    assert len(images)==49
    assert len([p for p in images if p.parent == artwork_root])==43
    assert len([p for p in images if p.parent == artwork_root/'beverages'])==6
    assert len([p for p in images if p.name.startswith('clean-')])==11
    from app.enbishan_menu_assets import MANIFEST, valid_bundled
    bundled_paths={entry['path'] for entry in MANIFEST.values()}
    actual_paths={'/' + p.relative_to(ROOT/'public').as_posix() for p in images}
    assert actual_paths == bundled_paths
    assert all(valid_bundled(path) for path in actual_paths)
