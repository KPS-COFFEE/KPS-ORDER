import pytest
from app.prelaunch_inventory import classification, inventory


def test_regulated_evidence_and_configuration_never_become_delete_candidates():
    for table in ('users','branches','products','fleet_vehicles','audit_events','fiscal_documents','order_branch_counters'):
        assert classification(table)=='PRESERVE'
    assert classification('new_unknown_ledger')=='UNCLASSIFIED_PRESERVE_UNTIL_REVIEWED'
    assert classification('orders')=='INDIVIDUAL_LEGAL_AND_TEST_REVIEW_REQUIRED'


def test_inventory_rejects_write_capable_transaction_before_inspecting_source():
    class Result:
        def fetchone(self):return {'transaction_read_only':'off'}
    class Connection:
        def execute(self,query):
            assert query=='SHOW transaction_read_only'
            return Result()
    with pytest.raises(ValueError,match='Read-only'):
        inventory(Connection())


def test_fiscal_table_closure_keeps_parent_and_child_evidence_without_expanding_shared_identity():
    from app.prelaunch_inventory import fiscal_retention_tables
    def edge(child, parent):
        return {'table_name':child, 'referenced_table':parent}
    graph = [edge('fiscal_documents','orders'), edge('fiscal_documents','refunds'),
             edge('orders','users'), edge('orders','branches'), edge('collections','orders'),
             edge('refunds','collections'), edge('collections','shifts'),
             edge('driver_evidence','shifts'), edge('sessions','users')]
    retained = set(fiscal_retention_tables(graph))
    assert {'fiscal_documents','orders','users','branches','collections','refunds','shifts','driver_evidence'} <= retained
    assert 'sessions' not in retained


@pytest.mark.postgres
def test_inventory_reads_postgres18_snapshot_without_mutation(database):
    from app import db
    with db.tx() as conn:
        assert int(conn.execute('SHOW server_version_num').fetchone()['server_version_num']) >= 180000
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        report = inventory(conn)
        assert report['cleanup_executable'] is False
        assert report['cleanup_rehearsed'] is False
        assert report['reset_authorized'] is False
        assert report['fiscal_evidence']['counts'] == {'invoice':0,'credit_note':0}
        assert len(report['fiscal_evidence']['sha256']) == 64
        assert all(item['approved_delete_rows']==0 for item in report['table_counts'])
        assert 'orders' in report['fiscal_retention_tables']
