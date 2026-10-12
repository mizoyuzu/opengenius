import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import expand_genuine_roots as runner
import probe_ytmusic_batch as collector
import genuine_root_recovery as recovery


class ScaledGenuineRootBatchLimitTests(unittest.TestCase):
    def test_scaled_bounds_and_existing_twenty_seed_configuration(self):
        self.assertTrue(runner.validate_batch_limits(20, 90))
        self.assertTrue(runner.validate_batch_limits(50, 225))
        self.assertTrue(runner.validate_batch_limits(100, 450))
        self.assertTrue(collector.validate_batch_limits(20, 90))
        self.assertTrue(collector.validate_batch_limits(50, 225))
        self.assertTrue(collector.validate_batch_limits(100, 450))

    def test_scaled_profile_requires_explicit_radio_only_source_and_collector(self):
        self.assertTrue(runner.validate_batch_profile(20, 90, None, None))
        self.assertFalse(runner.validate_batch_profile(50, 225, None, 'radio-only-v1'))
        self.assertFalse(runner.validate_batch_profile(50, 225,
                                                       'authenticated-watch-radio-only-v1', None))
        self.assertTrue(runner.validate_batch_profile(50, 225,
                                                      'authenticated-watch-radio-only-v1',
                                                      'radio-only-v1'))
        self.assertTrue(collector.validate_batch_profile(20, 90, 'related-v1', False))
        self.assertFalse(collector.validate_batch_profile(100, 450, 'related-v1', False))
        self.assertFalse(collector.validate_batch_profile(100, 450, 'radio-only-v1', True))
        self.assertTrue(collector.validate_batch_profile(100, 450, 'radio-only-v1', False))

    def test_recovery_preserves_legacy_manifests_and_guards_scaled_receipts(self):
        self.assertTrue(recovery.scaled_collection_manifest_compatible(20, 90, {}))
        valid = {'collector_collection_mode': 'radio-only-v1'}
        self.assertTrue(recovery.scaled_collection_manifest_compatible(100, 450, valid))
        self.assertFalse(recovery.scaled_collection_manifest_compatible(100, 450, {}))
        self.assertFalse(recovery.scaled_collection_manifest_compatible(100, 450,
                                                                       {**valid, 'seed_query_fallback': True}))

    def test_scaled_config_hash_is_distinct_and_old_budget_stays_valid(self):
        old = {'batch_size': 20, 'batch_request_budget': 90, 'deadline': None}
        scaled = {'batch_size': 100, 'batch_request_budget': 450, 'deadline': None,
                  'source_policy': 'authenticated-watch-radio-only-v1',
                  'collector_collection_mode': 'radio-only-v1'}
        old_hash = recovery.runner_config_sha256(old)
        scaled_hash = recovery.runner_config_sha256(scaled)
        self.assertNotEqual(old_hash, scaled_hash)
        self.assertEqual(len(old_hash), 64)
        self.assertEqual(len(scaled_hash), 64)
        with self.assertRaises(recovery.RecoveryUnsafe):
            recovery.runner_config_sha256(
                {'batch_size': 101, 'batch_request_budget': 451, 'deadline': None})

    def test_collector_accepts_a_full_hundred_record_exact_library_plan(self):
        tracks = [
            {'persistent_id': f'{number:016X}', 'title': f'Song {number}',
             'artist': f'Artist {number}', 'album': f'Album {number}',
             'duration_ms': 180000}
            for number in range(1, 101)
        ]
        plan = {'schema_version': 1, 'source_library_sha256': 'a' * 64,
                'seeds': [{'persistent_id': row['persistent_id'],
                           'series': 'automatic-frontier',
                           'sample_kind': 'automatic-frontier'} for row in tracks]}
        chosen = collector.select_planned_seeds(plan, tracks, 'a' * 64, 100)
        self.assertEqual([row['persistent_id'] for row in chosen],
                         [row['persistent_id'] for row in tracks])

    def test_over_budget_collector_results_fail_closed_before_promotion(self):
        completed = {'schema_version': 1, 'status': 'completed',
                     'account_authentication_verified': True,
                     'http_requests': 450, 'request_budget': 450}
        self.assertTrue(runner._valid_collector_batch(completed))
        for requests, status in ((451, 'completed'), (451, 'stopped')):
            over_budget = {**completed, 'http_requests': requests, 'status': status,
                           'error_type': 'RuntimeError',
                           'error_location': {'function': 'request'}}
            self.assertFalse(runner._valid_collector_batch(over_budget))

    def test_scaled_bounds_reject_values_above_campaign_ceiling(self):
        for validate in (runner.validate_batch_limits,
                         collector.validate_batch_limits):
            self.assertFalse(validate(101, 450))
            self.assertFalse(validate(100, 451))
            self.assertFalse(validate(True, 90))
            self.assertFalse(validate(20, 90.0))


if __name__ == '__main__':
    unittest.main()
