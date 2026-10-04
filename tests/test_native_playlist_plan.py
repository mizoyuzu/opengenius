import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_native_playlist_plan import prepare


class NativePlaylistPlanTests(unittest.TestCase):
    def setUp(self):
        self.pids = [f'{i:016X}' for i in (1, 2, 3)]
        self.digest = 'a' * 64
        self.manifest = {'tracks': [{'persistent_id': p} for p in self.pids]}
        self.report = {'source_library_sha256': self.digest, 'results': [
            {'root_pid': self.pids[0], 'playlist': [{'persistent_id': p} for p in self.pids]}]}

    def test_complete_order_preserved_without_mutating_inputs(self):
        before = copy.deepcopy((self.report, self.manifest))
        plan = prepare(self.report, self.manifest, [self.pids[0]], self.digest)
        self.assertEqual(plan['recommendation_playlists'][0]['persistent_ids'], self.pids)
        self.assertEqual((self.report, self.manifest), before)

    def test_missing_audio_refuses_silent_truncation(self):
        self.manifest['tracks'].pop()
        with self.assertRaisesRegex(ValueError, 'refusing partial'):
            prepare(self.report, self.manifest, [self.pids[0]], self.digest)

    def test_foreign_library_and_duplicate_members_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different source'):
            prepare(self.report, self.manifest, [self.pids[0]], 'b' * 64)
        self.report['results'][0]['playlist'].append({'persistent_id': self.pids[1]})
        with self.assertRaisesRegex(ValueError, 'order or membership'):
            prepare(self.report, self.manifest, [self.pids[0]], self.digest)

    def test_wrong_seed_order_and_ambiguous_roots_rejected(self):
        self.report['results'][0]['playlist'].reverse()
        with self.assertRaisesRegex(ValueError, 'order or membership'):
            prepare(self.report, self.manifest, [self.pids[0]], self.digest)
        self.report['results'].append(copy.deepcopy(self.report['results'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate recommendation root'):
            prepare(self.report, self.manifest, [self.pids[0]], self.digest)
