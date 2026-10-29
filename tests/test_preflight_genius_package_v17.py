import unittest

from scripts import preflight_genius_package_v17 as preflight


class PackageReadinessV17Tests(unittest.TestCase):
    def test_baseline_only_reports_original_bundle_and_accepted_source_parent(self):
        result = preflight.check_baseline()
        self.assertEqual(result['original_library_track_count'], 3004)
        self.assertEqual(result['physical_native_assignments'], 515)
        self.assertEqual(result['physical_native_nonempty_roots'], 159)
        self.assertEqual(result['physical_native_edges'], 3341)
        source = result['source_parent']
        self.assertEqual(source['base_total_genuine_roots'], 782)
        self.assertEqual(source['remaining_genuine_roots'], 218)
        self.assertEqual(source['unknown_roots'], 0)
        self.assertIn('pending', result['status'])


if __name__ == '__main__':
    unittest.main()
