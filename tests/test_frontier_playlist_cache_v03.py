import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('/tmp/opengenius-work-video-capture-runtime-v03')
sys.path.insert(0, str(RUNTIME / 'scripts'))

import work_track_video_bindings as bindings


class FrontierPlaylistCacheV03Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        packet = ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01'
        snapshot = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
        _, _, cls.official, cls.tracks, _ = bindings.load_binding_inputs(
            packet / 'candidate-bindings.json', packet / 'report.json',
            packet / 'hallab-official-page.html', snapshot, repo=ROOT)
        cls.cache = (ROOT / 'data/ytmusic/post870-kirby-playlist-direct-20261015-v04'
                     / 'requests/ae16d1c9d68a4b5a7cea47b4e9b9a1a1523c7a11d4781d5e02a59c7a6bb3ffd3.json')

    def test_actual_playlist_rows_rebind_as_metadata_only_hints(self):
        rows, receipt = bindings.load_frontier_playlist_cache(
            self.cache, self.official, self.tracks)
        self.assertEqual(len(rows), 99)
        self.assertEqual(receipt['strict_metadata_candidate_count'], 99)
        self.assertTrue(all(row['frontier_hint']['metadata_hint_only'] for row in rows))
        self.assertTrue(all(row['recording_identity_verified'] is False for row in rows))
        self.assertTrue(all(row['frontier_hint']['credit'] == 0 for row in rows))
        attempted = {item['persistent_id'] for item in json.loads(
            (ROOT / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0001'
             / 'candidate-graphs.json').read_text())['results']}
        self.assertEqual(sum(row['persistent_id'] not in attempted for row in rows), 55)

    def test_playlist_duration_projection_matches_watch_signature(self):
        bound, _ = bindings.load_frontier_playlist_cache(
            self.cache, self.official, self.tracks)
        source = json.loads(self.cache.read_text())['response']['tracks']
        candidate = next(row for row in bound if row['remote_length'])
        raw_row = source[candidate['response_position']]
        equivalent_watch_row = dict(raw_row)
        equivalent_watch_row['length'] = raw_row['duration']
        equivalent_watch_row.pop('duration', None)
        equivalent_watch_row.pop('duration_seconds', None)
        expected = bindings.bind_video_row(
            equivalent_watch_row, self.official, self.tracks)
        actual = {key: value for key, value in candidate.items() if key != 'frontier_hint'}
        expected['response_position'] = candidate['response_position']
        self.assertEqual(actual, expected)
        self.assertEqual(candidate['remote_length'], raw_row['duration'])

    def test_wrong_endpoint_and_claimed_credit_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'post870-kirby-playlist-direct-20261015-v04'
            reqdir = parent / 'requests'
            reqdir.mkdir(parents=True)
            cache_doc = json.loads(self.cache.read_text())
            summary_path = self.cache.parents[1] / 'summary.json'
            intent_path = self.cache.parents[1] / 'intent.json'
            shutil.copyfile(summary_path, parent / 'summary.json')
            shutil.copyfile(intent_path, parent / 'intent.json')

            bad_endpoint = dict(cache_doc, endpoint='get_watch_playlist')
            endpoint_path = reqdir / self.cache.name
            endpoint_path.write_text(json.dumps(bad_endpoint), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'exact saved get_playlist'):
                bindings.load_frontier_playlist_cache(
                    endpoint_path, self.official, self.tracks)

            endpoint_path.unlink()
            bad_summary = json.loads((parent / 'summary.json').read_text())
            bad_summary['root_credit'] = 1
            (parent / 'summary.json').write_text(json.dumps(bad_summary), encoding='utf-8')
            endpoint_path.write_text(self.cache.read_text(), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'summary does not seal'):
                bindings.load_frontier_playlist_cache(
                    endpoint_path, self.official, self.tracks)


if __name__ == '__main__':
    unittest.main()
