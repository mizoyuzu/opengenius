"""Offline candidate graph tests; actual Music core integration is opt-in."""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from emulate_ytmusic_candidates import candidate_graph, controlled_configs, run_experiment, synthetic_rows
from genius_format import pack_config, parse_config, parse_similarities
from music_identity_map import IdentityMap, build_map

EXECUTABLE = os.environ.get('OPENGENIUS_MUSIC_EXECUTABLE')


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': 'ROOT', 'title': 'Seed', 'artist': 'A', 'album': 'Seed album', 'duration_ms': 100000},
            {'persistent_id': 'TWO', 'title': 'Second', 'artist': 'B', 'album': 'Album', 'duration_ms': 101000},
            {'persistent_id': 'ONE', 'title': 'First', 'artist': 'A', 'album': 'Album', 'duration_ms': 102000},
        ]
        self.matcher = IdentityMap(build_map(self.tracks, 'hash'), self.tracks, 'hash')

    def row(self, pid, video, seed=False, relation='radio'):
        local = next(track for track in self.tracks if track['persistent_id'] == pid)
        return {'source': 'ytmusic', 'relation': relation, 'is_seed': seed,
                'position': 1, 'observed_at': 'original-time',
                'track': {'video_id': video, 'title': local['title'], 'artists': [{'name': local['artist']}],
                          'album': {'name': local['album']}, 'duration_seconds': local['duration_ms'] / 1000}}

    def snapshot(self):
        return {'library_sha256': 'hash', 'local_seed': {'persistent_id': 'STALE_OTHER_LIBRARY_PID'},
                'observations': [self.row('ROOT', 'seed-video', True), self.row('TWO', 'second-video'),
                                 self.row('ONE', 'first-video', relation='related'),
                                 self.row('TWO', 'second-video', relation='related')]}

    def test_graph_keeps_relation_order_repeats_and_current_root(self):
        graph = candidate_graph(self.snapshot(), self.matcher, 'hash')
        self.assertEqual(graph['root_pid'], 'ROOT')
        self.assertEqual(graph['ordered_target_pids'], ['TWO', 'ONE'])
        self.assertEqual(len(graph['observations']), 4)
        repeated = next(group for group in graph['candidate_groups'] if group['video_id'] == 'second-video')
        self.assertEqual(repeated['observation_indices'], [1, 3])
        self.assertEqual(repeated['identity_status'], 'unverified')

    def test_search_or_foreign_library_cannot_create_edges(self):
        snapshot = self.snapshot()
        snapshot['observations'][1]['relation'] = 'search_candidate'
        with self.assertRaisesRegex(ValueError, 'radio/related'):
            candidate_graph(snapshot, self.matcher, 'hash')
        snapshot = self.snapshot()
        snapshot['library_sha256'] = 'other-library'
        with self.assertRaisesRegex(ValueError, 'different Library'):
            candidate_graph(snapshot, self.matcher, 'hash')

    def test_root_requires_one_duration_supported_pid_even_with_album_preference(self):
        snapshot = self.snapshot()
        snapshot['observations'][0]['track'].pop('duration_seconds')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            candidate_graph(snapshot, self.matcher, 'hash')
        tracks = self.tracks + [{**self.tracks[0], 'persistent_id': 'OTHER_ROOT', 'album': 'Other release'}]
        matcher = IdentityMap(build_map(tracks, 'hash'), tracks, 'hash')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            candidate_graph(self.snapshot(), matcher, 'hash')

    def test_ambiguous_targets_have_no_edges_until_metadata_preference(self):
        tracks = self.tracks + [{**self.tracks[1], 'persistent_id': 'OTHER_TWO', 'album': 'Other release'}]
        matcher = IdentityMap(build_map(tracks, 'hash'), tracks, 'hash')
        snapshot = self.snapshot()
        for row in snapshot['observations']:
            if row['track']['video_id'] == 'second-video':
                row['track'].pop('album')
        graph = candidate_graph(snapshot, matcher, 'hash')
        self.assertEqual(graph['ordered_target_pids'], ['ONE'])
        snapshot['observations'][1]['track']['album'] = {'name': 'Album'}
        graph = candidate_graph(snapshot, matcher, 'hash')
        self.assertEqual(graph['ordered_target_pids'], ['TWO', 'ONE'])

    def test_ids_metadata_groups_and_directed_root_only_edges(self):
        graph = candidate_graph(self.snapshot(), self.matcher, 'hash')
        ids, metadata, similarities, mapping = synthetic_rows(graph, self.matcher)
        self.assertEqual(ids, {'ONE': 0x70000001, 'ROOT': 0x70000002, 'TWO': 0x70000003})
        words = {pid: struct.unpack('<4Q', metadata[identifier]) for pid, identifier in ids.items()}
        self.assertTrue(all(len(blob) == 32 for blob in metadata.values()))
        self.assertTrue(all(word[0] == 0 for word in words.values()))
        self.assertEqual(words['ONE'][1], words['ROOT'][1])
        self.assertNotEqual(words['ONE'][1], words['TWO'][1])
        self.assertEqual(words['ONE'][2], words['TWO'][2])
        self.assertEqual(parse_similarities(similarities[ids['ROOT']])[1], [ids['TWO'], ids['ONE']])
        self.assertEqual(parse_similarities(similarities[ids['ONE']])[1], [])
        self.assertEqual(parse_similarities(similarities[ids['TWO']])[1], [])
        self.assertTrue(all(row['identity_status'] == 'unverified' for row in mapping))

    def test_song_groups_use_canonical_credits_and_original_title_not_album(self):
        tracks = self.tracks + [{**self.tracks[2], 'persistent_id': 'REISSUE', 'artist': 'Alias A', 'album': 'Different album'}]
        document = build_map(tracks, 'hash')
        document['artist_alias_groups'] = [{'enabled': True, 'aliases': ['A', 'Alias A']}]
        matcher = IdentityMap(document, tracks, 'hash')
        graph = {'root_pid': 'ROOT', 'ordered_target_pids': ['ONE', 'REISSUE']}
        ids, metadata, _, _ = synthetic_rows(graph, matcher)
        one = struct.unpack('<4Q', metadata[ids['ONE']])
        reissue = struct.unpack('<4Q', metadata[ids['REISSUE']])
        self.assertEqual(one[1], reissue[1])
        self.assertEqual(one[3], reissue[3])
        self.assertNotEqual(one[2], reissue[2])

    def test_profiles_change_only_requested_filter_types(self):
        config = pack_config({'version': 2, 'filters': [
            {'type': 1, 'parameters': [20, 50, 10, 10]},
            {'type': 2, 'metadata_index': 0, 'records': [(7, [7])]},
            {'type': 3, 'parameters': [1, 2, 3, 4]}], 'flags': 0, 'result_words': [10, 20]})
        profiles = controlled_configs(config)
        self.assertEqual([item['type'] for item in parse_config(profiles['without-compatible-genre'])['filters']], [1, 3])
        self.assertEqual([item['type'] for item in parse_config(profiles['relations-only'])['filters']], [1])
        self.assertEqual(parse_config(profiles['relations-only'])['filters'][0]['parameters'], [20, 50, 10, 10])

    @unittest.skipUnless(EXECUTABLE, 'Set OPENGENIUS_MUSIC_EXECUTABLE for binary integration tests')
    def test_actual_core_consumes_candidate_relation_order(self):
        graph = candidate_graph(self.snapshot(), self.matcher, 'hash')
        config = pack_config({'version': 2, 'filters': [{'type': 1, 'parameters': [20, 50, 10, 10]}],
                              'flags': 0, 'result_words': [10, 20]})
        report = run_experiment(EXECUTABLE, config, graph, self.matcher)
        for run in report['runs']:
            self.assertEqual(run['playlist_pids'], ['ROOT', 'TWO', 'ONE'])
            self.assertEqual(run['identity_status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
