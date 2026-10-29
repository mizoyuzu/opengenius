import unittest

from scripts import audit_final_ytmusic_source as auditor


class SourceAwareAuditGateTests(unittest.TestCase):
    def test_strict_ytmusic_goal_rejects_841_source_roots_in_1000_total(self):
        source = {
            'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                       'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 1000},
        }
        with self.assertRaisesRegex(ValueError, 'fewer than 1000 roots.*ytmusic-backed'):
            auditor._require_goal(source, 'ytmusic-backed')

    def test_explicit_total_genuine_metric_accepts_1000_fully_classified_roots(self):
        source = {
            'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                       'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 1000},
        }
        self.assertEqual(auditor._require_goal(source, 'total-genuine'), 1000)

    def test_native_ytmusic_overlap_is_unioned_once(self):
        source = {
            'counts': {'native_only': 158, 'ytmusic_source_backed': 842,
                       'native_ytmusic_overlap': 1, 'unknown': 0, 'total_active': 1000},
        }
        self.assertEqual(auditor._goal_root_count(source, 'total-genuine'), 1000)
        self.assertEqual(auditor._goal_root_count(source, 'ytmusic-backed'), 842)

    def test_unknown_roots_are_rejected_for_either_metric(self):
        source = {
            'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                       'native_ytmusic_overlap': 0, 'unknown': 1, 'total_active': 1001},
        }
        for metric in ('ytmusic-backed', 'total-genuine'):
            with self.subTest(metric=metric), self.assertRaisesRegex(ValueError, 'exact final-root partition'):
                auditor._goal_root_count(source, metric)


if __name__ == '__main__':
    unittest.main()
