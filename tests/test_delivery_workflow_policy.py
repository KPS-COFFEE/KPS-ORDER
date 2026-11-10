"""Policy boundaries for real driver pickup without an absent branch employee."""
from copy import deepcopy
import unittest

from app.delivery_workflow import configured_workflow, is_legacy_manager_pickup


DEFAULT_CASES = [('enbishan', {}, 'manager_dispatch'),
 ('enbishan', None, 'manager_dispatch'),
 ('kps', {}, 'branch_fulfillment'),
 ('another-brand', {}, 'branch_fulfillment'),
 ('enbishan', {'delivery_workflow': 'branch_fulfillment'}, 'branch_fulfillment'),
 ('enbishan', {'delivery_workflow': 'branch_without_manager'}, 'branch_without_manager'),
 ('kps', {'delivery_workflow': 'manager_dispatch'}, 'manager_dispatch'),
 ('enbishan', {'delivery_workflow': []}, 'manager_dispatch')]
BOUNDARIES = [('order', {'brand_id': 'kps'}),
 ('order', {'service': 'pickup'}),
 ('order', {'service': 'car'}),
 ('order', {'preparation_status': 'new'}),
 ('order', {'preparation_status': 'cancelled'}),
 ('order', {'preparation_status': 'rejected'}),
 ('order', {'preparation_status': 'completed'}),
 ('order', {'preparation_status': 'handed_over'}),
 ('order', {'completed_at': '2026-10-25T10:00:00Z'}),
 ('order', {'test_void': {'reason': 'voided'}}),
 ('order', {'branch_id': 'other-branch'}),
 ('order', {'id': 'another-order'}),
 ('order', {'snapshot': {'delivery_period': 'morning', 'delivery_workflow': 'branch_fulfillment'}}),
 ('order', {'snapshot': {'delivery_workflow': 'branch_without_manager'}}),
 ('order', {'snapshot': {'delivery_workflow': 'manager_dispatch'}}),
 ('order', {'snapshot': 'invalid'}),
 ('task', {'status': 'assigned'}),
 ('task', {'status': 'problem'}),
 ('task', {'status': 'picked_up'}),
 ('task', {'status': 'delivered'}),
 ('task', {'status': 'cancelled'}),
 ('task', {'status': 'transferred'}),
 ('task', {'picked_up_at': '2026-10-25T10:00:00Z'}),
 ('task', {'delivered_at': '2026-10-25T10:00:00Z'}),
 ('task', {'driver_id': 'other-driver'}),
 ('task', {'shift_id': 'other-shift'}),
 ('shift', {'status': 'closing'}),
 ('shift', {'status': 'submitted'}),
 ('shift', {'status': 'closed'}),
 ('shift', {'kind': 'manager'}),
 ('shift', {'brand_id': 'kps'}),
 ('shift', {'user_id': 'other-driver'}),
 ('shift', {'branch_ids': []}),
 ('shift', {'delivery_window_snapshot': {'period': 'morning'}}),
 ('user', {'role': 'owner'}),
 ('user', {'role': 'delivery_manager'}),
 ('user', {'id': 'other-driver'}),
 ('user', {'brand_id': 'kps'}),
 ('user', {'branch_ids': []}),
 ('settings', {'delivery_workflow': 'branch_fulfillment'}),
 ('settings', {'delivery_workflow': 'branch_without_manager'})]


def legacy_case():
    return deepcopy({'order': {'id': 'order', 'brand_id': 'enbishan', 'service': 'delivery', 'branch_id': 'branch', 'preparation_status': 'accepted', 'completed_at': None, 'test_void': None, 'snapshot': {'delivery_workflow': 'branch_fulfillment'}}, 'task': {'id': 'task', 'order_id': 'order', 'driver_id': 'driver', 'shift_id': 'shift', 'status': 'accepted', 'picked_up_at': None, 'delivered_at': None}, 'shift': {'id': 'shift', 'user_id': 'driver', 'brand_id': 'enbishan', 'kind': 'driver', 'status': 'open', 'branch_ids': ['branch'], 'delivery_window_snapshot': {'period': 'evening'}}, 'user': {'id': 'driver', 'role': 'driver', 'brand_id': 'enbishan', 'branch_ids': ['branch']}, 'settings': {}})


class DeliveryWorkflowPolicyTest(unittest.TestCase):
    def test_saved_modes_take_precedence_and_only_enbishan_defaults_to_manager(self):
        for brand, settings, expected in DEFAULT_CASES:
            with self.subTest(brand=brand, settings=settings):
                self.assertEqual(configured_workflow(brand, settings), expected)

    def test_actual_assigned_driver_adopts_policy_without_mutating_any_record(self):
        for stage in ('accepted', 'preparing', 'ready'):
            for settings in ({}, {'delivery_workflow': 'manager_dispatch'}):
                with self.subTest(stage=stage, settings=settings):
                    case = legacy_case()
                    case['order']['preparation_status'] = stage
                    case['settings'] = settings
                    before = deepcopy(case)
                    self.assertTrue(is_legacy_manager_pickup(**case))
                    self.assertEqual(case, before)
                    self.assertIsNone(case['task']['picked_up_at'])
                    self.assertEqual(case['order']['snapshot']['delivery_workflow'], 'branch_fulfillment')

    def test_bridge_does_not_widen_stage_identity_scope_or_explicit_owner_policy(self):
        for record, changes in BOUNDARIES:
            with self.subTest(record=record, changes=changes):
                case = legacy_case()
                case[record].update(changes)
                before = deepcopy(case)
                self.assertFalse(is_legacy_manager_pickup(**case))
                self.assertEqual(case, before)

    def test_old_snapshot_without_mode_is_eligible_but_cannot_change_morning(self):
        case = legacy_case()
        case['order']['snapshot'] = {}
        self.assertTrue(is_legacy_manager_pickup(**case))
        case['order']['snapshot']['delivery_period'] = 'morning'
        self.assertFalse(is_legacy_manager_pickup(**case))

    def test_absent_identities_cannot_match_each_other(self):
        case = legacy_case()
        for record, key in [('user', 'id'), ('shift', 'user_id'), ('task', 'driver_id')]:
            case[record].pop(key)
        self.assertFalse(is_legacy_manager_pickup(**case))


if __name__ == '__main__':
    unittest.main()
