"""Only immutable source parsing is cached; request settings remain fresh."""
import json

from app import invoice_profiles as profiles


def test_capture_reuses_supplied_settings_without_reading_database(monkeypatch, tmp_path):
    path = tmp_path / 'matches.json'
    path.write_text(json.dumps({'matches': [{'brand_id': 'kps', 'branch_id': 'branch', 'google_address': 'Address'}]}))
    monkeypatch.setattr(profiles, 'BRANCH_MATCHES_PATH', path)
    class NoDatabase:
        def execute(self, *args):
            raise AssertionError('Redundant settings query')
    config = {'invoice_profiles': {'assignments': {'branch': 'seller'},
                                  'entities': {'seller': {'legal_name': 'Before', 'vat_enabled': False}}}}
    first = profiles.capture(NoDatabase(), 'kps', 'branch', brand_settings=config)
    assert first['address'] == 'Address' and first['legal_name'] == 'Before'
    config['invoice_profiles']['entities']['seller']['legal_name'] = 'After'
    assert profiles.capture(NoDatabase(), 'kps', 'branch', brand_settings=config)['legal_name'] == 'After'
    assert first['legal_name'] == 'Before'
    assert profiles.capture(NoDatabase(), 'OTHER', 'branch', brand_settings={})['address'] == ''


def test_address_cache_refreshes_after_source_replacement(monkeypatch, tmp_path):
    path = tmp_path / 'matches.json'
    def write(target, address):
        target.write_text(json.dumps({'matches': [{'brand_id': 'kps', 'branch_id': 'branch', 'google_address': address}]}))
    write(path, 'First')
    monkeypatch.setattr(profiles, 'BRANCH_MATCHES_PATH', path)
    before = profiles._branch_addresses.cache_info()
    for _ in range(3):
        assert profiles.capture(None, 'kps', 'branch', brand_settings={})['address'] == 'First'
    after = profiles._branch_addresses.cache_info()
    assert after.misses == before.misses + 1 and after.hits == before.hits + 2
    replacement = tmp_path / 'replacement.json'
    write(replacement, 'Other')
    replacement.replace(path)
    assert profiles.capture(None, 'kps', 'branch', brand_settings={})['address'] == 'Other'


def test_missing_source_does_not_serve_stale_address(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(profiles, 'BRANCH_MATCHES_PATH', tmp_path / 'missing.json')
    with pytest.raises(FileNotFoundError):
        profiles.capture(None, 'kps', 'branch', brand_settings={})


def test_other_brand_never_inherits_kps_source_address(monkeypatch, tmp_path):
    path = tmp_path / 'matches.json'
    path.write_text(json.dumps({'matches': [{'brand_id': 'enbishan', 'branch_id': 'branch', 'google_address': 'Not approved fiscal address'}]}))
    monkeypatch.setattr(profiles, 'BRANCH_MATCHES_PATH', path)
    assert profiles.capture(None, 'enbishan', 'branch', brand_settings={})['address'] == ''
