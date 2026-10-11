"""Exact operational identities, workbook provenance and honest Maps destinations."""
from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app import admin, branch_locations, catalog, db

EXPECTED_PLACE_IDS = {
    1: 'ChIJ4YCabfY51z8RRvgEX468j7c', 2: 'ChIJE0dZrh5B1z8RpGSHUypO04s',
    3: 'ChIJTTqSsnM_1z8RLcbfIQNNGEQ', 4: 'ChIJaYGp-6tB1z8Rb8bBNgUZjfI',
    5: 'ChIJt40TGPIH1z8R3adbLg_CRaU', 6: 'ChIJXeEbPYu7zD8Rx5b5LNB9Q7s',
    7: 'ChIJRYLq1WT9ST4RLaB_Fa1OlBE', 8: 'ChIJewUEvecT1T8RhfkIudi8hZg',
    9: 'ChIJZdnl4lI51z8RDyi016qRLCA', 10: 'ChIJ_YqVSfMv1z8RiMv2Io4YDgg',
    11: 'ChIJwSVdlTkdzD8R3abKRHhv0_0', 12: 'ChIJOcElampH1z8RTzgR8HgAO2E',
    13: 'ChIJ6Y_gAH3DfxURmBPLD-wOWhs', 14: 'ChIJkZIme8L7ST4RExsODhjY9f0',
    15: 'ChIJ_zX9D1DHKj4RvcG-zzHUzzQ', 16: 'ChIJVxEa7p_tgRUR7WzWZroZnlA',
    17: 'ChIJQXOnj4ZFdhURTjeuegRGvOo', 18: 'ChIJz4xSleONYhUR2k3VuwcFagc',
    19: 'ChIJ2c3SY1ZnZBURSWdXIaLYMxw', 20: 'ChIJF2e1GDkrfBUROBGkEmfkl-M',
}


def test_reviewed_workbook_mapping_is_by_label_not_row_order_or_business_code():
    rows = branch_locations.workbook_matches()
    assert {row['branch_no']: row['place_id'] for row in rows} == EXPECTED_PLACE_IDS
    assert len({row['place_id'] for row in rows}) == 20
    assert sum(row['confirmed_match'] for row in rows) == 14
    assert {row['branch_no'] for row in rows if not row['confirmed_match']} == {4, 9, 11, 12, 13, 14}
    by_number = {row['branch_no']: row for row in rows}
    assert by_number[9]['source_row'] == 6
    assert by_number[5]['source_row'] == 7
    assert by_number[1]['place_id'] != by_number[9]['place_id']
    assert by_number[1]['internal_label'] == 'المروج'
    assert by_number[9]['internal_label'] == 'المروج 9 - كشك/DT'
    assert all(catalog.safe_map_url(row['map_url']) == row['map_url'] for row in rows)


@pytest.mark.parametrize('tamper', ['source', 'label', 'identity', 'city', 'uncertainty', 'duplicate_place', 'url',
                                    'destination_place', 'destination_camera', 'destination_coordinates'])
def test_changed_source_or_unconfirmed_evidence_fails_closed(monkeypatch, tamper):
    document = json.loads((db.ROOT / 'data/branch_google_matches.json').read_text())
    if tamper == 'source':
        document['metadata']['source_sha256'] = '0' * 64
    elif tamper == 'label':
        document['matches'][0]['internal_label'] = 'المروج 9 - كشك/DT'
    elif tamper == 'identity':
        document['matches'][0]['source_id'] = 'another-source'
    elif tamper == 'city':
        document['matches'][0]['canonical_city'] = 'another-city'
    elif tamper == 'uncertainty':
        document['matches'][8]['confirmed_match'] = True
    elif tamper == 'duplicate_place':
        document['matches'][1]['place_id'] = document['matches'][0]['place_id']
    elif tamper == 'url':
        document['matches'][0]['map_url'] = document['matches'][8]['map_url']
    elif tamper == 'destination_place':
        document['matches'][0]['destination_evidence']['place_id'] = EXPECTED_PLACE_IDS[9]
    elif tamper == 'destination_camera':
        document['matches'][0]['destination_evidence']['destination_pin_data'] = '@28.3986693,45.9836647,16z'
    elif tamper == 'destination_coordinates':
        document['matches'][0]['destination_evidence']['location']['lat'] = 28
    original = json.loads
    def load(value):
        result = original(value)
        return deepcopy(document) if isinstance(result, dict) and 'matches' in result else result
    monkeypatch.setattr(branch_locations.json, 'loads', load)
    with pytest.raises(RuntimeError):
        branch_locations.workbook_matches()


@pytest.fixture
def unbound_branches(database):
    # Reset only this fixture's UUID schema; production import never clears data.
    with db.tx() as conn:
        conn.execute('DELETE FROM owner_branch_location_decisions')
        conn.execute("UPDATE branches SET settings=(settings - 'location_source' - 'location_verified') || %s "
                     "WHERE brand_id='kps' AND settings->'map_binding_source'->>'sha256'=%s",
                     (db.json({'location': None}), branch_locations.WORKBOOK_SHA256))
        conn.execute("UPDATE branches SET settings=settings - 'place_id' - 'map_url' "
                     "- 'map_url_verified' - 'map_binding_source' WHERE brand_id='kps'")
    return database


@pytest.mark.postgres
def test_existing_twenty_rows_receive_fourteen_exact_bindings_without_activation(unbound_branches):
    with db.tx() as conn:
        before = conn.execute("SELECT * FROM branches WHERE brand_id='kps' ORDER BY branch_no").fetchall()
        branch_locations.apply_workbook_matches(conn)
        after = conn.execute("SELECT * FROM branches WHERE brand_id='kps' ORDER BY branch_no").fetchall()
        decisions = conn.execute('SELECT * FROM owner_branch_location_decisions ORDER BY branch_no').fetchall()
        assert [row['id'] for row in after] == [row['id'] for row in before]
        assert len(after) == 20
        assert sum(row['result'] == 'applied' for row in decisions) == 14
        assert sum(row['result'] == 'requires_owner_confirmation' for row in decisions) == 6
        matches = {row['branch_no']: row for row in branch_locations.workbook_matches()}
        for old, branch in zip(before, after):
            settings = branch['settings']
            match = matches[branch['branch_no']]
            evidence = match.get('destination_evidence')
            coordinate_changes = ({'location': evidence['location'], 'location_verified': True,
                                   'location_source': evidence['source_url']} if evidence else {})
            for key, value in old['settings'].items():
                # Only an explicitly verified destination may replace initial
                # coordinate fields. Every service and other setting survives.
                assert settings[key] == coordinate_changes.get(key, value)
            if evidence:
                assert settings['location'] == evidence['location']
                assert catalog.location_verified(branch)
                assert settings['location_source'] == evidence['source_url']
                assert parse_qs(urlsplit(settings['location_source']).query)['destination_place_id'] == [EXPECTED_PLACE_IDS[branch['branch_no']]]
                # Actual imported values must round-trip through the owner form
                # contract without dict-to-string evidence corruption.
                admin.validate_branch_settings(
                    {key: settings[key] for key in ('location', 'location_verified', 'location_source',
                                                    'place_id', 'map_url', 'map_url_verified')},
                    branch['city'],
                )
            else:
                assert settings['location'] is None
                assert not catalog.location_verified(branch)
            assert all(not settings[key] for key in ('pickup_enabled', 'car_enabled', 'delivery_enabled'))
            if branch['branch_no'] in branch_locations.PENDING_BRANCHES:
                assert catalog.directions_url(branch) is None
            else:
                directions = urlsplit(catalog.directions_url(branch))
                assert directions.path == '/maps/dir/'
                query = parse_qs(directions.query)
                assert query['destination_place_id'] == [EXPECTED_PLACE_IDS[branch['branch_no']]]
                assert query['api'] == ['1']
                assert query['travelmode'] == ['driving']
                point = evidence['location'] if evidence else None
                destination = f"{point['lat']},{point['lng']}" if point else branch['name']
                assert query['destination'] == [destination]
                assert settings['map_binding_source']['sha256'] == branch_locations.WORKBOOK_SHA256
        one, nine = after[0], after[8]
        assert one['id'] != nine['id']
        assert parse_qs(urlsplit(catalog.directions_url(one)).query)['destination_place_id'] == [EXPECTED_PLACE_IDS[1]]
        assert catalog.directions_url(nine) is None
        # Verified coordinates never bypass the owner's disabled services.
        nearest = catalog.rank_nearest_branches(after, {'lat': 28, 'lng': 45}, 'pickup', {})
        assert nearest['branches'] == []


@pytest.mark.postgres
def test_owner_location_and_explicit_removal_are_preserved(unbound_branches):
    owner_map = {'place_id': 'ChIJOWNERCONFIRMED0001', 'map_url_verified': True}
    owner_removed = {'place_id': None, 'map_url': None, 'map_url_verified': False}
    owner_coordinates = {'location': {'lat': 28.45, 'lng': 45.9}, 'location_verified': True}
    with db.tx() as conn:
        for number, settings in [(1, owner_map), (2, owner_removed), (3, owner_coordinates)]:
            conn.execute('UPDATE branches SET settings=settings || %s WHERE id=%s',
                         (db.json(settings), f'kps-branch-{number}'))
        branch_locations.apply_workbook_matches(conn)
        decisions = conn.execute('SELECT branch_no,result FROM owner_branch_location_decisions WHERE branch_no<=3').fetchall()
        assert all(row['result'] == 'preserved_owner_location' for row in decisions)
        for number, settings in [(1, owner_map), (2, owner_removed), (3, owner_coordinates)]:
            stored = conn.execute('SELECT settings FROM branches WHERE id=%s', (f'kps-branch-{number}',)).fetchone()['settings']
            assert all(stored[key] == value for key, value in settings.items())


@pytest.mark.postgres
def test_identity_mismatch_and_existing_duplicate_binding_are_not_imported(unbound_branches):
    with db.tx() as conn:
        conn.execute("UPDATE branches SET source_id='unrelated-import' WHERE id='kps-branch-1'")
        conn.execute("UPDATE branches SET city='غير مطابق' WHERE id='kps-branch-2'")
        conn.execute("UPDATE branches SET branch_no=21 WHERE id='kps-branch-3'")
        conn.execute("UPDATE branches SET settings=settings || %s WHERE id='kps-branch-19'",
                     (db.json({'place_id': EXPECTED_PLACE_IDS[5], 'map_url_verified': True}),))
        branch_locations.apply_workbook_matches(conn)
        decisions = {row['branch_no']: row['result'] for row in
                     conn.execute('SELECT branch_no,result FROM owner_branch_location_decisions').fetchall()}
        assert decisions[1] == decisions[2] == decisions[3] == 'skipped_identity_mismatch'
        assert decisions[5] == 'skipped_existing_place_binding'
        assert decisions[19] == 'preserved_owner_location'
        assert conn.execute("SELECT count(*) AS n FROM branches WHERE settings->>'place_id'=%s",
                            (EXPECTED_PLACE_IDS[5],)).fetchone()['n'] == 1


@pytest.mark.postgres
@pytest.mark.parametrize('remove_pin', [False, True], ids=['remove_place_binding_only', 'remove_complete_location'])
def test_restart_preserves_later_owner_edits_and_does_not_duplicate_audits(unbound_branches, remove_pin):
    with db.tx() as conn:
        branch_locations.apply_workbook_matches(conn)
        before = conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='branch.owner_workbook_maps_binding'").fetchone()['n']
        owner_change = {'place_id': None, 'map_url': None, 'map_url_verified': False}
        if remove_pin:
            owner_change.update(location=None, location_verified=False)
        conn.execute("UPDATE branches SET settings=settings || %s WHERE id='kps-branch-1'",
                     (db.json(owner_change),))
        changed_settings = conn.execute("SELECT settings FROM branches WHERE id='kps-branch-1'").fetchone()['settings']
    db.migrate()
    with db.tx() as conn:
        branch_locations.apply_workbook_matches(conn)
        edited = conn.execute("SELECT * FROM branches WHERE id='kps-branch-1'").fetchone()
        assert edited['settings'] == changed_settings
        if remove_pin:
            assert not catalog.location_verified(edited)
            assert catalog.directions_url(edited) is None
        else:
            assert catalog.location_verified(edited)
            query = parse_qs(urlsplit(catalog.directions_url(edited)).query)
            assert query == {'api': ['1'], 'destination': ['28.3986693,45.9836647'], 'travelmode': ['driving']}
        assert conn.execute('SELECT count(*) AS n FROM owner_branch_location_decisions').fetchone()['n'] == 26
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='branch.owner_workbook_maps_binding'").fetchone()['n'] == before


@pytest.mark.postgres
def test_owner_confirmation_publishes_six_exact_places_without_fabricating_pins(unbound_branches):
    with db.tx() as conn:
        branch_locations.apply_workbook_matches(conn)
        before = conn.execute("SELECT * FROM branches WHERE brand_id='kps' ORDER BY branch_no").fetchall()
        branch_locations.apply_owner_confirmed_matches(conn)
        after = conn.execute("SELECT * FROM branches WHERE brand_id='kps' ORDER BY branch_no").fetchall()
        assert [b['id'] for b in before] == [b['id'] for b in after]
        for old, branch in zip(before, after):
            query = parse_qs(urlsplit(catalog.directions_url(branch)).query)
            assert query['destination_place_id'] == [EXPECTED_PLACE_IDS[branch['branch_no']]]
            if branch['branch_no'] in branch_locations.PENDING_BRANCHES:
                assert not catalog.location_verified(branch)
                assert branch['settings']['location'] is None
                assert all(branch['settings'][key] == value for key, value in old['settings'].items())
            else:
                assert branch == old
            assert not any(branch['settings'][key] for key in ('pickup_enabled', 'car_enabled', 'delivery_enabled'))
        original = conn.execute('SELECT * FROM owner_branch_location_decisions WHERE decision_id LIKE %s',
                                (branch_locations.DECISION_ID + ':%',)).fetchall()
        assert sum(d['result'] == 'requires_owner_confirmation' for d in original) == 6
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='branch.owner_confirmed_maps_binding'").fetchone()['n'] == 6
        branch_locations.apply_owner_confirmed_matches(conn)
        assert conn.execute('SELECT count(*) AS n FROM owner_branch_location_decisions').fetchone()['n'] == 26
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='branch.owner_confirmed_maps_binding'").fetchone()['n'] == 6
        conn.execute("UPDATE branches SET settings=settings || %s WHERE id='kps-branch-9'",
                     (db.json({'place_id': None, 'map_url': None, 'map_url_verified': False}),))
        branch_locations.apply_owner_confirmed_matches(conn)
        nine = conn.execute("SELECT * FROM branches WHERE id='kps-branch-9'").fetchone()
        assert catalog.directions_url(nine) is None


@pytest.mark.postgres
def test_owner_confirmation_preserves_conflicting_edits_and_rejects_duplicate_places(unbound_branches):
    with db.tx() as conn:
        conn.execute("UPDATE branches SET settings=settings || %s WHERE id='kps-branch-4'",
                     (db.json({'place_id': None, 'map_url_verified': False}),))
        conn.execute("UPDATE branches SET settings=settings || %s WHERE id='kps-branch-20'",
                     (db.json({'place_id': EXPECTED_PLACE_IDS[9]}),))
        conn.execute("UPDATE branches SET source_id='wrong-source' WHERE id='kps-branch-11'")
        branch_locations.apply_owner_confirmed_matches(conn)
        decisions = {d['branch_no']: d['result'] for d in conn.execute(
            'SELECT * FROM owner_branch_location_decisions').fetchall()}
        assert decisions[4] == 'preserved_owner_location'
        assert decisions[9] == 'skipped_existing_place_binding'
        assert decisions[11] == 'skipped_identity_mismatch'
