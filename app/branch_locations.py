"""One-time Google Maps bindings from the owner's existing-profile workbook.

Workbook labels are explicitly matched to canonical operational branch numbers.
Uncertain source rows are evidence only. No coordinates, coverage, hours, service
activation or duplicate branch records are inferred from a Google Place ID.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from . import db

WORKBOOK_SHA256 = 'c748eeb39586fe2e024005165c160a20e2017722ffa1b93db5b7c216b73f3e66'
DECISION_ID = 'kps-branch-maps-owner-final-2026-10-10'
PENDING_BRANCHES = frozenset({4, 9, 11, 12, 13, 14})
# These labels, rather than workbook row indices or KPS-00n business codes,
# connect the provided sheet to the existing canonical identities.
LABEL_BRANCH_NUMBERS = {
    'المروج': 1, 'الخالدية': 2, 'النايفية': 3, 'العزيزية': 4,
    'الذيبية': 5, 'النعيرية - العزيزية': 6, 'الدمام - أحد': 7,
    'الأرطاوية': 8, 'المروج 9 - كشك/DT': 9, 'القيصومة - الخالدية': 10,
    'الخفجي - العزيزية': 11, 'الباطن': 12, 'الزلفي - الفاخرية': 13,
    'الدمام - الجامعيين': 14, 'المجمعة': 15, 'عنيزة': 16,
    'حائل': 17, 'شعبة نصاب': 18, 'رفحاء': 19, 'قبه': 20,
}


def workbook_matches():
    """Validate the reviewed, public-only evidence manifest before DB mutation."""
    document = json.loads((db.ROOT / 'data' / 'branch_google_matches.json').read_text())
    metadata = document['metadata']
    if (metadata.get('source_sha256') != WORKBOOK_SHA256
            or metadata.get('source_sheet') != '03-Existing Matches'
            or metadata.get('decision_id') != DECISION_ID
            or metadata.get('coordinates_available') is not False):
        raise RuntimeError('Branch Maps evidence source does not match the reviewed workbook.')
    rows = document['matches']
    canonical = {row['branch_no']: row for row in
                 json.loads((db.ROOT / 'data' / 'branches.json').read_text())['branches']}
    if len(rows) != 20 or {row.get('branch_no') for row in rows} != set(range(1, 21)):
        raise RuntimeError('Branch Maps evidence must contain each existing operational number once.')
    place_ids = set()
    for row in rows:
        number = row['branch_no']
        original = canonical[number]
        if LABEL_BRANCH_NUMBERS.get(row.get('internal_label')) != number:
            raise RuntimeError('Branch Maps label does not match its operational identity.')
        if (row.get('branch_id') != original['id'] or row.get('brand_id') != original['brand_id']
                or row.get('source_id') != original['source_id']
                or row.get('canonical_city') != original['city']
                or row.get('canonical_name') != original['name']):
            raise RuntimeError('Branch Maps canonical source identity changed.')
        expected_confirmed = number not in PENDING_BRANCHES
        if (row.get('confirmed_match') is not expected_confirmed
                or expected_confirmed != (row.get('match_confidence') == 'مطابقة واضحة')
                or not expected_confirmed and not row.get('conflict')):
            raise RuntimeError('Unconfirmed branch Maps evidence cannot be promoted automatically.')
        place_id = row.get('place_id')
        if (not isinstance(place_id, str) or not re.fullmatch(r'ChI[A-Za-z0-9_-]{7,247}', place_id)
                or place_id in place_ids):
            raise RuntimeError('Branch Maps Place ID is invalid or duplicated.')
        place_ids.add(place_id)
        parsed = urlsplit(row.get('map_url') or '')
        if (parsed.scheme != 'https' or parsed.netloc != 'www.google.com'
                or parsed.path != '/maps/search/' or parsed.fragment
                or parse_qs(parsed.query) != {'api': ['1'], 'query_place_id': [place_id]}):
            raise RuntimeError('Branch Maps source URL does not identify its exact Place ID.')
        if row.get('destination_evidence'):
            _destination_location(row)
    return rows


def _destination_location(match):
    """Use a verified destination pin, never the map camera's @lat,lng."""
    evidence = match.get('destination_evidence')
    if not evidence:
        return None
    source = urlsplit(evidence.get('source_url') or '')
    query = parse_qs(source.query)
    location = evidence.get('location') or {}
    pin = re.fullmatch(r'!1s0x[0-9a-f]+:0x[0-9a-f]+!2m2!1d(-?\d+(?:\.\d+)?)!2d(-?\d+(?:\.\d+)?)',
                       evidence.get('destination_pin_data') or '')
    if (not match['confirmed_match'] or evidence.get('place_id') != match['place_id']
            or evidence.get('source') != 'google_maps_direction_destination'
            or evidence.get('verification_method') != 'actual_google_maps_directions_page_destination_data'
            or source.scheme != 'https' or source.netloc != 'www.google.com'
            or source.path != '/maps/dir/' or source.fragment
            or query.get('destination_place_id') != [match['place_id']]
            or query.get('api') != ['1'] or query.get('travelmode') != ['driving']
            or not pin or type(location.get('lat')) not in (int, float)
            or type(location.get('lng')) not in (int, float)
            or not -90 <= location['lat'] <= 90 or not -180 <= location['lng'] <= 180
            or float(pin[1]) != location['lng'] or float(pin[2]) != location['lat']):
        raise RuntimeError('Branch Maps coordinates do not match the confirmed destination pin.')
    return location


def _owner_location_present(settings):
    # An explicit owner removal or unverified replacement is also an override.
    # Initial immutable seeds have only location=None and no map binding fields.
    return (settings.get('location') is not None
            or any(key in settings for key in
                   ('place_id', 'map_url', 'map_url_verified', 'location_verified', 'location_source')))


def apply_workbook_matches(conn):
    """Apply once under the migration transaction and preserve later owner edits."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (764832617,))
    for match in workbook_matches():
        decision = f"{DECISION_ID}:branch:{match['branch_no']}"
        conn.execute(
            'INSERT INTO owner_branch_location_decisions '
            '(decision_id,brand_id,branch_id,branch_no,source_id,workbook_sha256,evidence) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (decision, match['brand_id'], match['branch_id'], match['branch_no'],
             match['source_id'], WORKBOOK_SHA256, db.json(match)),
        )
        recorded = conn.execute(
            'SELECT * FROM owner_branch_location_decisions WHERE decision_id=%s FOR UPDATE',
            (decision,),
        ).fetchone()
        if recorded['evaluated_at'] is not None:
            continue
        if (recorded['workbook_sha256'] != WORKBOOK_SHA256 or recorded['evidence'] != match
                or any(recorded[key] != match[key]
                       for key in ('brand_id', 'branch_id', 'branch_no', 'source_id'))):
            raise RuntimeError('Branch Maps decision does not match reviewed evidence.')
        branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s FOR UPDATE',
                              (match['branch_id'], match['brand_id'])).fetchone()
        result = 'skipped_identity_mismatch'
        if (branch and branch['branch_no'] == match['branch_no']
                and branch['source_id'] == match['source_id']
                and branch['city'] == match['canonical_city']):
            result = 'requires_owner_confirmation'
            if match['confirmed_match']:
                result = 'preserved_owner_location'
                if not _owner_location_present(branch['settings']):
                    duplicate = conn.execute(
                        "SELECT id FROM branches WHERE id<>%s AND brand_id=%s "
                        "AND settings->>'place_id'=%s LIMIT 1",
                        (branch['id'], branch['brand_id'], match['place_id']),
                    ).fetchone()
                    result = 'skipped_existing_place_binding'
                    if not duplicate:
                        location = _destination_location(match)
                        patch = {
                            'place_id': match['place_id'], 'map_url': match['map_url'],
                            'map_url_verified': True,
                            'map_binding_source': {
                                'file': 'KPS_Google_Business_Consolidation.xlsx',
                                'sheet': '03-Existing Matches', 'row': match['source_row'],
                                'sha256': WORKBOOK_SHA256, 'internal_label': match['internal_label'],
                                'match_confidence': match['match_confidence'],
                                'independent_google_destination_verified': location is not None,
                            },
                        }
                        if location:
                            # The existing owner form/API accept a text source.
                            # Full pin evidence remains in the decision ledger.
                            patch.update(location=location, location_verified=True,
                                         location_source=match['destination_evidence']['source_url'])
                        conn.execute('UPDATE branches SET settings=settings || %s,updated_at=now() WHERE id=%s',
                                     (db.json(patch), branch['id']))
                        db.audit(conn, None, 'branch.owner_workbook_maps_binding', 'branch', branch['id'],
                                 {'decision_id': decision, 'branch_no': branch['branch_no'],
                                  'source_sha256': WORKBOOK_SHA256, 'place_id': match['place_id'],
                                  'coordinates_imported': location is not None, 'services_activated': False})
                        result = 'applied'
        conn.execute('UPDATE owner_branch_location_decisions SET result=%s,evaluated_at=now() WHERE decision_id=%s',
                     (result, decision))
