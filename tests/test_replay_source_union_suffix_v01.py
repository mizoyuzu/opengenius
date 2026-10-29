"""Real suffix replay; only the already sealed, costly prefix replay is stubbed."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v15-static-composed-absence-v01')
sys.path.insert(0, str(RUNTIME / 'scripts'))
os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = json.loads((RUNTIME / 'runtime-manifest.json').read_text())['trusted_data_root']
import merge_rebased_radio_growth as merger
from music_identity_map import IdentityMap, load_library
from radio_snapshot_proof import radio_snapshot_proof_cache_scope, radio_snapshot_proof_validation_pass
from seed_video_evidence import seed_video_selector_cache_scope

spec = importlib.util.spec_from_file_location(
    'suffix_under_test', RUNTIME / 'scripts/replay_source_union_suffix_v01.py')
suffix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suffix)


class SealedPrefixFixture:
    def replay_union_checkpoint(self, *args):
        graph = suffix.read(suffix.PREFIX / 'accepted-graphs.json')
        return (suffix.read(suffix.PREFIX / 'state.json'), graph['accepted_roots'],
                copy.deepcopy(graph['growth_runs']), suffix.PREFIX_HASH)


class ActualSuffixTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v14-source-union-live/batch-0001/growth'
        self.accepted = suffix.read(self.directory / 'accepted-graphs.json')
        self.tracks, self.library_hash, _ = load_library(track_snapshot=ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json')
        identity = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
        self.identity_hash = suffix.digest(identity)
        self.matcher = IdentityMap(suffix.read(identity), self.tracks, self.library_hash)

    def replay(self, directory, accepted):
        with radio_snapshot_proof_cache_scope(), seed_video_selector_cache_scope(), radio_snapshot_proof_validation_pass():
            return suffix.replay(directory, accepted, {}, [], {}, self.matcher,
                                 self.library_hash, self.identity_hash, merger, SealedPrefixFixture())

    def test_actual_four_exports_replay_without_any_collector_calls(self):
        state, roots, runs, digest = self.replay(self.directory, self.accepted)
        self.assertEqual(sum(bool(r['ordered_target_pids']) for r in roots), 782)
        self.assertEqual(state['state_sha256'], '2d4b89587e84620b5d56d32a0829aef8fbccfdd2cc203eb02c2e3436fcaa8e69')
        self.assertEqual(len(runs), 3)
        self.assertEqual(digest, suffix.digest(self.directory / 'accepted-graphs.json'))

    def test_resealed_export_with_forged_parent_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ('accepted-graphs.json', 'state.json', 'relations.json', 'accepted-evaluation.json'):
                value = suffix.read(self.directory / name)
                if name == 'accepted-graphs.json':
                    value['direct_parent_state_sha256'] = '0' * 64
                (directory / name).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'export differs'):
                self.replay(directory, suffix.read(directory / 'accepted-graphs.json'))

    def test_suffix_replay_threads_prior_snapshot_receipts_to_each_run(self):
        captured = []
        original = merger._validate_run_receipt

        def recording(run, tracks, matcher, library_hash, identity_hash,
                      prior_snapshot_inputs=()):
            captured.append(list(prior_snapshot_inputs))
            return original(run, tracks, matcher, library_hash, identity_hash,
                            prior_snapshot_inputs=prior_snapshot_inputs)

        merger._validate_run_receipt = recording
        try:
            self.replay(self.directory, self.accepted)
        finally:
            merger._validate_run_receipt = original
        self.assertTrue(captured)
        self.assertTrue(all(isinstance(rows, list) for rows in captured))
        self.assertTrue(any(rows for rows in captured),
                        'source-union prefix receipts were not carried into suffix replay')
        for rows in captured:
            keys = [(row['path'], row['sha256']) for row in rows]
            self.assertEqual(len(keys), len(set(keys)))


if __name__ == '__main__':
    unittest.main()
