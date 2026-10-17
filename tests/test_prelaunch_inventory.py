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
