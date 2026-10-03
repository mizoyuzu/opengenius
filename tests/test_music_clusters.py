"""Library-scoped user rule tests using invented local metadata only."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_clusters import classify_tracks, main, off_vocal_marker, scope_graphs


class ClusterTests(unittest.TestCase):
    def setUp(self):
        self.digest = 'a' * 64
        self.tracks = [
            {'persistent_id': '0000000000000001', 'title': 'Song', 'artist': 'Artist', 'album': 'THE IDOLM@STER SHINY COLORS'},
            {'persistent_id': '0000000000000002', 'title': 'Song (OffVocal)', 'artist': 'Artist', 'album': 'THE IDOLM@STER SHINY COLORS'},
            {'persistent_id': '0000000000000003', 'title': 'Opening Instrumental', 'artist': 'Composer', 'album': 'Novel OST'},
        ]
        self.config = {'schema_version': 1, 'source_library_sha256': self.digest, 'rules': [
            {'name': 'Family', 'match': {'album_contains': ['THE IDOLM@STER']}, 'tags': ['アイマス']},
            {'name': 'Brand', 'match': {'album_contains': ['SHINY COLORS']}, 'tags': ['シャニマス'], 'kind': 'vocal'},
        ], 'overrides': {}}

    def test_cumulative_family_brand_tags_kind_precedence_and_special_guard(self):
        result = classify_tracks(self.config, self.tracks, self.digest)
        self.assertEqual(result['0000000000000001']['tags'], ['アイマス', 'シャニマス'])
        self.assertEqual(result['0000000000000001']['kind'], 'vocal')
        self.assertEqual(result['0000000000000002']['kind'], 'off_vocal')
        self.assertEqual(result['0000000000000002']['evidence'][-1]['source'], 'special_version_marker')
        self.assertEqual(result['0000000000000003']['kind'], 'unknown')
        later = copy.deepcopy(self.config)
        later['rules'].append({'match': {'artist_is': ['Artist']}, 'tags': ['後ルール'], 'kind': 'bgm'})
        result = classify_tracks(later, self.tracks, self.digest)
        self.assertEqual(result['0000000000000001']['kind'], 'bgm')
        self.assertEqual(result['0000000000000002']['kind'], 'off_vocal')

    def test_match_conditions_and_lists_or_use_nfkc_casefold_and_exact_artist(self):
        config = {'schema_version': 1, 'source_library_sha256': self.digest, 'rules': [
            {'match': {'album_contains': ['absent', 'ｓｈｉｎｙ'], 'artist_is': ['Other', 'ＡＲＴＩＳＴ'],
                       'title_contains': ['missing', 'ＳＯＮＧ'], 'persistent_ids': ['0000000000000001']}, 'tags': ['matched']},
            {'match': {'album_contains': ['SHINY'], 'artist_is': ['Art']}, 'tags': ['bad-substring']},
        ]}
        result = classify_tracks(config, self.tracks, self.digest)
        self.assertEqual(result['0000000000000001']['tags'], ['matched'])
        self.assertEqual(result['0000000000000002']['tags'], [])
        self.assertEqual(result['0000000000000001']['kind'], 'unknown')

    def test_override_replaces_tags_and_kind_last_without_mutating_inputs(self):
        config = copy.deepcopy(self.config)
        config['overrides'] = {'0000000000000002': {'tags': ['個別'], 'kind': 'vocal'},
                               '0000000000000001': {'tags': []}}
        before_config, before_tracks = copy.deepcopy(config), copy.deepcopy(self.tracks)
        result = classify_tracks(config, self.tracks, self.digest)
        self.assertEqual(result['0000000000000002']['tags'], ['個別'])
        self.assertEqual(result['0000000000000002']['kind'], 'vocal')
        self.assertEqual(result['0000000000000001']['tags'], [])
        result['0000000000000002']['evidence'][-1]['tags'].append('output-only')
        self.assertEqual(config, before_config)
        self.assertEqual(self.tracks, before_tracks)

    def test_auto_versions_are_title_only_and_instrumental_is_not_off_vocal(self):
        for title in ('Song (Off Vocal)', 'Song (OffVocal)', 'Song (OFF-VOCAL)', 'Song (off_vocal)',
                      'Song (Karaoke)', 'Song (カラオケ)', 'Song (オリジナル・カラオケ)'):
            with self.subTest(title=title):
                self.assertIsNotNone(off_vocal_marker(title))
        for title in ('Instrumental', 'The Off Vocalists', 'karaokesong', 'Normal song'):
            with self.subTest(title=title):
                self.assertIsNone(off_vocal_marker(title))
        tracks = [{'persistent_id': '0000000000000004', 'title': 'Normal song', 'artist': 'Karaoke Artist', 'album': 'Off Vocal OST'}]
        config = {'schema_version': 1, 'source_library_sha256': self.digest, 'rules': []}
        self.assertEqual(classify_tracks(config, tracks, self.digest)[tracks[0]['persistent_id']]['kind'], 'unknown')

    def test_invalid_config_hash_conditions_ids_and_types_are_rejected(self):
        invalid = []
        for key, value in (('schema_version', True), ('source_library_sha256', 'b' * 64), ('rules', True), ('overrides', []), ('unknown', 1)):
            document = copy.deepcopy(self.config); document[key] = value; invalid.append(document)
        rules = [
            {'match': {}, 'tags': ['x']}, {'match': {'regex': ['.*']}, 'tags': ['x']},
            {'match': {'title_contains': []}, 'tags': ['x']}, {'match': {'artist_is': [True]}, 'tags': ['x']},
            {'match': {'artist_is': 'Artist'}, 'tags': ['x']}, {'match': {'artist_is': ['Artist']}, 'tags': [' ']},
            {'match': {'artist_is': ['Artist']}, 'tags': ['x'], 'kind': True},
            {'match': {'artist_is': ['Artist']}, 'tags': ['x'], 'kind': 'instrumental'},
            {'match': {'artist_is': ['Artist']}, 'tags': ['x'], 'name': ''},
            {'match': {'persistent_ids': ['F' * 16]}, 'tags': ['x']},
            {'match': {'persistent_ids': ['0000000000000001', '0000000000000001']}, 'tags': ['x']},
            {'match': {'persistent_ids': ['abcdef0123456789']}, 'tags': ['x']},
            {'match': {'title_contains': ['Song']}, 'tags': ['x'], 'metadata': {}},
        ]
        for rule in rules:
            invalid.append({**copy.deepcopy(self.config), 'rules': [rule]})
        for override in ({}, {'kind': 'invalid'}, {'tags': True}, {'tags': [False]}, {'unknown': 'value'}):
            invalid.append({**copy.deepcopy(self.config), 'overrides': {'0000000000000001': override}})
        invalid.append({**copy.deepcopy(self.config), 'overrides': {'F' * 16: {'kind': 'vocal'}}})
        for index, document in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(ValueError):
                classify_tracks(document, self.tracks, self.digest)
        with self.assertRaises(ValueError):
            classify_tracks(self.config, [*self.tracks, self.tracks[0]], self.digest)

    def test_cli_uses_saved_metadata_hashes_and_exclusive_output_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot, config, output = root / 'tracks.json', root / 'clusters.json', root / 'result.json'
            snapshot.write_text(json.dumps({'source_sha256': self.digest, 'tracks': self.tracks}))
            config.write_text(json.dumps(self.config))
            original_snapshot, original_config = snapshot.read_bytes(), config.read_bytes()
            argv = ['clusters', '--track-snapshot', str(snapshot), '--config', str(config), '--output', str(output)]
            with patch.object(sys, 'argv', argv), patch('sys.stdout', new=io.StringIO()), \
                    patch('music_identity_map.decode_musicdb', side_effect=AssertionError('No Library read')):
                main()
            report = json.loads(output.read_text())
            self.assertEqual(report['config_sha256'], hashlib.sha256(original_config).hexdigest())
            self.assertEqual(report['track_snapshot_sha256'], hashlib.sha256(original_snapshot).hexdigest())
            self.assertEqual(report['classifications']['0000000000000002']['kind'], 'off_vocal')
            self.assertFalse(report['recording_identity_inferred'])
            self.assertEqual(report['network_requests'], 0)
            self.assertEqual(snapshot.read_bytes(), original_snapshot)
            self.assertEqual(config.read_bytes(), original_config)
            with patch.object(sys, 'argv', argv), patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    main()
            config.write_text('{"schema_version":1,"schema_version":1}')
            with patch.object(sys, 'argv', [*argv[:-1], str(root / 'new-result.json')]):
                with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
                    main()

    def test_scope_uses_all_normalized_tags_kind_and_preserves_observations(self):
        classifications = {
            'A': {'tags': ['アイマス', 'SHINY'], 'kind': 'vocal'},
            'B': {'tags': ['アイマス', 'SHINY'], 'kind': 'vocal'},
            'C': {'tags': ['アイマス'], 'kind': 'vocal'},
            'D': {'tags': ['アイマス', 'SHINY'], 'kind': 'off_vocal'},
        }
        graphs = [
            {'root_pid': 'A', 'ordered_target_pids': ['B', 'C', 'D'], 'observations': [{'nested': ['source']}],
             'candidate_groups': [{'video_id': 'original'}]},
            {'root_pid': 'D', 'ordered_target_pids': ['B'], 'observations': []},
            {'root_pid': 'B', 'ordered_target_pids': ['D', 'A'], 'observations': []},
        ]
        before = copy.deepcopy(graphs)
        filtered, summary = scope_graphs(graphs, classifications, tags=['アイマス', 'ｓｈｉｎｙ'], kind='vocal')
        self.assertEqual([graph['root_pid'] for graph in filtered], ['A', 'B'])
        self.assertEqual([graph['ordered_target_pids'] for graph in filtered], [['B'], ['A']])
        self.assertEqual(summary['excluded_root_pids'], ['D'])
        self.assertEqual(summary['removed_target_count'], 4)
        self.assertEqual(summary['tags'], ['アイマス', 'shiny'])
        self.assertEqual(filtered[0]['observations'], before[0]['observations'])
        self.assertEqual(filtered[0]['candidate_groups'], before[0]['candidate_groups'])
        filtered[0]['observations'][0]['nested'].append('output-only')
        self.assertEqual(graphs, before)

    def test_scope_rejects_unknown_tags_invalid_kinds_and_missing_pids_before_filtering(self):
        classifications = {'A': {'tags': ['Known'], 'kind': 'vocal'}, 'B': {'tags': [], 'kind': 'unknown'}}
        graphs = [{'root_pid': 'A', 'ordered_target_pids': ['B'], 'observations': []}]
        for tags, kind in ((['missing'], None), ([''], None), ([' '], None), ('Known', None),
                           (['Known', 'KNOWN'], None), ([], 'instrumental'), ([], True)):
            with self.subTest(tags=tags, kind=kind), self.assertRaises(ValueError):
                scope_graphs(graphs, classifications, tags, kind)
        with self.assertRaisesRegex(ValueError, 'No roots'):
            scope_graphs(graphs, classifications, kind='bgm')
        with self.assertRaisesRegex(ValueError, 'classification'):
            scope_graphs([*graphs, {'root_pid': 'B', 'ordered_target_pids': ['missing']}],
                         classifications, tags=['Known'])
        with self.assertRaisesRegex(ValueError, 'classification'):
            scope_graphs([{'root_pid': 'missing', 'ordered_target_pids': []}], classifications)
        filtered, summary = scope_graphs(graphs, classifications)
        self.assertEqual(filtered, graphs)
        self.assertIsNot(filtered[0], graphs[0])
        self.assertEqual(summary['removed_target_count'], 0)


if __name__ == '__main__':
    unittest.main()
