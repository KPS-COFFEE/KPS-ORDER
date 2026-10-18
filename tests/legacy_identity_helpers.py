"""Explicit synthetic legacy tenant for retained email-token security contracts.

KPS never opts out of mandatory SMS. These rows exercise the separately supported
non-SMS policy with synthetic contacts; no runtime guard is patched or bypassed.
"""
from app import db
from tests.test_core import _make_user as core_make_user

BRAND = 'legacy-security-fixture'
BRANCHES = ['legacy-security-branch-1', 'legacy-security-branch-5']


def seed_legacy_brand():
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES(%s,'Synthetic legacy security tenant') ON CONFLICT DO NOTHING", (BRAND,))
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES(%s,'{\"customer_phone_required\":false}') ON CONFLICT DO NOTHING", (BRAND,))
        for number in (1, 5, 7):
            conn.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES(%s,%s,%s,%s,'Synthetic branch','Synthetic city') ON CONFLICT DO NOTHING", (f'legacy-security-branch-{number}', number, str(number), BRAND))
        for channel in ('guest', 'email', 'sms', 'whatsapp'):
            conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING", (BRAND, channel, 'active' if channel == 'guest' else 'unconfigured', channel == 'guest'))


def make_legacy_user(role='customer', branches=None, **kwargs):
    seed_legacy_brand()
    scope = [branch.replace('kps-branch-', 'legacy-security-branch-') for branch in branches or []]
    return core_make_user(role, brand=BRAND, branches=scope, **kwargs)
