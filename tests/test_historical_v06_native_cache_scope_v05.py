"""Cheap guard tests for the isolated historical v06 cache-context wrapper."""
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


V05 = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v05-c6-cache-v01')
V06 = Path('/tmp/opengenius-final-campaign-runtime-v06')
V10 = Path('/tmp/opengenius-final-campaign-runtime-v10-native-index-compat-v01')


class HistoricalV06CacheScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_path = list(sys.path)
        cls.old_root = os.environ.get('OPENGENIUS_TRUSTED_DATA_ROOT')
        v10_manifest = json.loads((V10 / 'runtime-manifest.json').read_bytes())
        os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = v10_manifest['trusted_data_root']
        sys.path.insert(0, str(V10 / 'scripts'))
        sys.path.insert(0, str(V05 / 'scripts'))
        spec = importlib.util.spec_from_file_location(
            '_test_v05_source_union_merge',
            V05 / 'scripts/merge_rebased_radio_growth.py')
        cls.merge = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.merge
        spec.loader.exec_module(cls.merge)
        cls.saved = {name: sys.modules.pop(name, None)
                     for name in ('radio_snapshot_proof', 'seed_video_evidence')}
        sys.path.insert(0, str(V06 / 'scripts'))
        cls.v06_proof = importlib.import_module('radio_snapshot_proof')
        cls.v06_selector = importlib.import_module('seed_video_evidence')

    @classmethod
    def tearDownClass(cls):
        for name in ('radio_snapshot_proof', 'seed_video_evidence'):
            sys.modules.pop(name, None)
        for name, module in cls.saved.items():
            if module is not None:
                sys.modules[name] = module
        sys.modules.pop('_test_v05_source_union_merge', None)
        sys.path[:] = cls.old_path
        if cls.old_root is None:
            os.environ.pop('OPENGENIUS_TRUSTED_DATA_ROOT', None)
        else:
            os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = cls.old_root

    def pinned_module_hashes(self):
        return {
            'scripts/radio_snapshot_proof.py': self.merge.sha(
                (V06 / 'scripts/radio_snapshot_proof.py').read_bytes()),
            'scripts/seed_video_evidence.py': self.merge.sha(
                (V06 / 'scripts/seed_video_evidence.py').read_bytes()),
        }

    def test_frozen_verifier_runs_inside_exact_v06_cache_objects(self):
        class Probe:
            def verify_native_alias_child_report(self, **kwargs):
                return (
                    self_outer.v06_proof._ACTIVE_VERIFICATION_CACHE.get() is not None,
                    self_outer.v06_proof._ACTIVE_VERIFICATION_PASS.get() is not None,
                    self_outer.v06_selector._ACTIVE_SELECTOR_INDEX.get() is not None,
                    kwargs,
                )

        self_outer = self
        result = self.merge._run_historical_v06_native_verifier(
            Probe(), V06 / 'scripts', self.pinned_module_hashes(), sample='fixed')
        self.assertEqual(result[:3], (True, True, True))
        self.assertEqual(result[3], {'sample': 'fixed'})
        self.assertIsNone(self.v06_proof._ACTIVE_VERIFICATION_CACHE.get())
        self.assertIsNone(self.v06_proof._ACTIVE_VERIFICATION_PASS.get())
        self.assertIsNone(self.v06_selector._ACTIVE_SELECTOR_INDEX.get())

    def test_changed_or_wrong_path_cache_modules_are_rejected_before_verifier(self):
        class NeverCall:
            def verify_native_alias_child_report(self, **kwargs):
                raise AssertionError('verifier must not run for a mismatched closure')

        hashes = self.pinned_module_hashes()
        bad_hashes = dict(hashes)
        bad_hashes['scripts/radio_snapshot_proof.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'pinned v06 closure'):
            self.merge._run_historical_v06_native_verifier(
                NeverCall(), V06 / 'scripts', bad_hashes)

        sys.modules['radio_snapshot_proof'] = self.saved['radio_snapshot_proof']
        try:
            with self.assertRaisesRegex(ValueError, 'pinned v06 closure'):
                self.merge._run_historical_v06_native_verifier(
                    NeverCall(), V06 / 'scripts', hashes)
        finally:
            sys.modules['radio_snapshot_proof'] = self.v06_proof

    def test_v05_dispatcher_routes_only_to_pinned_v04_adapter_then_rejects_bad_run(self):
        v04 = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v04')
        helper = v04 / 'scripts/project_radio_source_union_v04.py'
        manifest = v04 / 'runtime-manifest.json'
        transition = {
            'adapter_code': {'path': str(helper),
                             'sha256': self.merge.sha(helper.read_bytes())},
            'trusted_v11_runtime_manifest': {
                'path': str(manifest), 'sha256': self.merge.sha(manifest.read_bytes())},
        }
        bad_typed_run = {
            'producer': 'opengenius-radio-typed-source-union-run-v1',
            'source_rematch_transition': transition,
        }
        accepted = {
            'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
            'growth_runs': [{}, bad_typed_run],
        }
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp)
            (previous / 'accepted-graphs.json').write_text(json.dumps(accepted))
            with self.assertRaisesRegex(ValueError, 'wrong producer, policy'):
                self.merge._replay_previous_growth(
                    previous, None, None, None, None, None, None)


if __name__ == '__main__':
    unittest.main()
