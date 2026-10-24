import unittest
from tools.feature_release_preflight import assess


class FeatureReleasePreflightTests(unittest.TestCase):
    sha = 'a' * 40

    def manifest(self):
        return {'KPS_MULTIBRAND_ENABLED': 'true',
                'KPS_CENTRAL_OPERATIONS_ENABLED': 'true',
                'KPS_MULTIBRAND_ACCEPTANCE_SHA': self.sha,
                'KPS_MULTIBRAND_OWNER_APPROVAL_SHA': self.sha}

    def test_current_release_passes(self):
        self.assertTrue(assess(self.sha, self.manifest())['approved'])

    def test_both_approvals_must_match_candidate_not_previous_release(self):
        for key in ('KPS_MULTIBRAND_ACCEPTANCE_SHA', 'KPS_MULTIBRAND_OWNER_APPROVAL_SHA'):
            with self.subTest(key=key):
                values = self.manifest()
                values[key] = 'b' * 40
                self.assertFalse(assess(self.sha, values)['approved'])

    def test_enabled_brand_feature_also_requires_both_approvals(self):
        values = self.manifest()
        values['KPS_GIFT_ORDERS_ENBISHAN_ENABLED'] = 'true'
        self.assertFalse(assess(self.sha, values)['approved'])
        values['KPS_GIFT_ORDERS_ENBISHAN_ACCEPTANCE_SHA'] = self.sha
        values['KPS_GIFT_ORDERS_ENBISHAN_OWNER_APPROVAL_SHA'] = self.sha
        self.assertTrue(assess(self.sha, values)['approved'])

    def test_old_runtime_pin_is_rejected(self):
        values = self.manifest()
        values['RUNTIME_REVISION'] = 'b' * 40
        self.assertFalse(assess(self.sha, values)['approved'])

    def test_missing_state_and_ambiguous_flags_are_rejected(self):
        for value in (None, True, 'TRUE', ''):
            values = self.manifest()
            if value is None:
                del values['KPS_MULTIBRAND_ENABLED']
            else:
                values['KPS_MULTIBRAND_ENABLED'] = value
            self.assertFalse(assess(self.sha, values)['approved'])

    def test_central_cannot_silently_disable_multibrand(self):
        values = self.manifest()
        values['KPS_MULTIBRAND_ENABLED'] = 'false'
        self.assertFalse(assess(self.sha, values)['approved'])

    def test_sensitive_keys_and_invalid_revision_are_rejected(self):
        values = self.manifest()
        values['DATABASE_URL'] = 'must-not-be-logged'
        report = assess(self.sha, values)
        self.assertFalse(report['approved'])
        self.assertNotIn('must-not-be-logged', str(report))
        self.assertFalse(assess('latest', self.manifest())['approved'])


if __name__ == '__main__':
    unittest.main()
