"""Execute the shipped option controls and prove guest ownership remains opaque."""
from pathlib import Path
import subprocess
import pytest
from app import auth, db


def test_direct_options_use_existing_validation_and_isolate_units():
    subprocess.run(['node', 'tests/test_enbishan_journey.cjs'], cwd=Path(__file__).resolve().parents[1], check=True)


def test_guest_ordering_policy_is_brand_scoped(database):
    with db.tx() as conn:
        conn.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{customer_phone_required}','true') WHERE brand_id='kps'")
        # The guest rollout must not relax KPS mandatory verification.
        assert auth.customer_phone_required(conn, 'kps') is True
        guest={'id':'synthetic-guest','brand_id':'kps','role':'customer','identity_kind':'guest',
               'display_name':'Synthetic Guest','phone':'+966501112233'}
        with pytest.raises(Exception) as exc:
            auth.assert_customer_order_eligible(conn, guest)
        assert 'CUSTOMER_PHONE_VERIFICATION_REQUIRED' in str(exc.value)
        # The existing guest contract accepts a valid contact only when its
        # brand policy explicitly permits it; a contact never proves identity.
        conn.execute("UPDATE identity_channels SET state='active',enabled=true WHERE brand_id='kps' AND channel='guest'")
        auth.assert_customer_order_eligible(conn, guest, phone_required=False)
        assert not auth.can_access_order(guest, {'customer_id':'another-guest','brand_id':'kps','customer':{'phone':guest['phone']}}, conn)
