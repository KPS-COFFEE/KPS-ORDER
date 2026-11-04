"""Real-database release preservation, one-time display decisions and recovery."""
import pytest

from app import db


@pytest.mark.postgres
def test_release_aborts_and_rolls_back_an_unexpected_existing_user_change(database, monkeypatch):
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,role,brand_id,display_name) VALUES('release-test-customer','customer','kps','عميل اختبار محفوظ')")
    original = db.apply_migrations

    def unexpected_change(conn, **kwargs):
        original(conn, **kwargs)
        conn.execute("UPDATE users SET display_name='unexpected migration overwrite' WHERE id='release-test-customer'")

    monkeypatch.setattr(db, 'apply_migrations', unexpected_change)
    with pytest.raises(RuntimeError, match='existing operational data changed'):
        db.migrate()
    with db.tx() as conn:
        assert conn.execute("SELECT display_name FROM users WHERE id='release-test-customer'").fetchone()['display_name'] == 'عميل اختبار محفوظ'


@pytest.mark.postgres
def test_branch_nine_rename_is_identity_safe_and_never_resets_later_owner_edits(client):
    with db.tx() as conn:
        one = conn.execute("SELECT * FROM branches WHERE id='kps-branch-1'").fetchone()
        nine = conn.execute("SELECT * FROM branches WHERE id='kps-branch-9'").fetchone()
        assert one['name'] == 'حفر الباطن - المروج'
        assert (nine['name'], nine['branch_no'], nine['source_id']) == ('حفرالباطن - المروج - درايف ثرو', 9, 'apex-canonical-branch-no:9')
        assert one['id'] != nine['id']
        conn.execute("UPDATE branches SET name='اسم لاحق اعتمده المالك' WHERE id='kps-branch-9'")
    db.migrate()
    with db.tx() as conn:
        assert conn.execute("SELECT name FROM branches WHERE id='kps-branch-9'").fetchone()['name'] == 'اسم لاحق اعتمده المالك'
        decision = conn.execute('SELECT * FROM owner_display_decisions').fetchone()
        assert decision['result'] == 'applied' and decision['evaluated_at']
        evidence = conn.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' ORDER BY created_at DESC LIMIT 1").fetchone()['details']
        assert evidence['verified'] and evidence['tables_checked'] >= 20
        assert 'users' in evidence['checks'] and 'sessions' in evidence['checks']
    public = client.get('/ready').json()['data_preservation']
    assert public['verified'] and public['checked_tables'] >= 20
    assert 'checks' not in public  # detailed counts/hash evidence stays private
