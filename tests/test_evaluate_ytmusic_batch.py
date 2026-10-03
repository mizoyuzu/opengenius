"""Offline shared-graph evaluation; actual-core scale fixture is opt-in."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_ytmusic_batch import (InvalidObservation, collect_graphs, evaluate_graphs, jaccard,
                                    merge_graphs, observation_paths, shared_rows, validate_observations)
from emulate_ytmusic_candidates import candidate_graph
from genius_format import pack_config, parse_similarities
from music_identity_map import IdentityMap, build_map

EXECUTABLE = os.environ.get('OPENGENIUS_MUSIC_EXECUTABLE')


def config():
    return pack_config({'version': 2, 'filters': [{'type': 1, 'parameters': [20, 50, 10, 10]}],
                        'flags': 0, 'result_words': [10, 20]})


class FakeCore:
    instances = []

    def __init__(self, executable, controlled, metadata, similarities):
        self.metadata, self.similarities = metadata, similarities
        self.calls, self.steps = [], 0
        self.instances.append(self)

    def generate(self, root, limit):
        self.calls.append(root)
        self.steps += 1
        targets = parse_similarities(self.similarities[root])[1]
        return {'result_genius_ids': [f'{value:016X}' for value in [root, *targets][:limit]]}


class BatchEvaluationTests(unittest.TestCase):
    def setUp(self):
        FakeCore.instances = []
        self.tracks = [{'persistent_id': f'{index:016X}', 'title': f'Song {index}',
                        'artist': 'A' if index in (1, 3) else 'B', 'album': 'Album',
                        'duration_ms': 100000} for index in range(1, 5)]
        self.matcher = IdentityMap(build_map(self.tracks, 'hash'), self.tracks, 'hash')

    def snapshot(self, root, targets):
        rows = []
        for index, pid_number in enumerate([root, *targets]):
            track = self.tracks[pid_number - 1]
            rows.append({'source': 'ytmusic', 'relation': 'radio' if index < 2 else 'related',
                         'position': index + 1, 'is_seed': index == 0, 'observed_at': '2026-10-03T00:00:00+00:00',
                         'track': {'video_id': f'video{pid_number:06}', 'title': track['title'],
                                   'artists': [{'name': track['artist']}], 'album': {'name': 'Album'},
                                   'duration_seconds': 100}})
        return {'schema_version': 1, 'library_sha256': 'hash', 'selected_video_id': f'video{root:06}', 'observations': rows}

    def graph(self, root, targets):
        snapshot = self.snapshot(root, targets)
        validate_observations(snapshot, 'hash')
        return candidate_graph(snapshot, self.matcher, 'hash')

    def test_shared_space_merges_same_root_in_order_without_reverse_edges(self):
        graphs = [self.graph(1, [3]), self.graph(1, [4, 3]), self.graph(2, [4])]
        roots = merge_graphs(graphs)
        self.assertEqual(roots[0]['ordered_target_pids'], [f'{3:016X}', f'{4:016X}'])
        self.assertEqual(roots[0]['observation_count'], 5)
        self.assertEqual(roots[0]['candidate_video_count'], 2)
        ids, metadata, similarities, mapping = shared_rows(roots, self.matcher)
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(metadata), 4)
        self.assertEqual(parse_similarities(similarities[ids[f'{1:016X}']])[1], [ids[f'{3:016X}'], ids[f'{4:016X}']])
        self.assertEqual(parse_similarities(similarities[ids[f'{2:016X}']])[1], [ids[f'{4:016X}']])
        self.assertEqual(parse_similarities(similarities[ids[f'{3:016X}']])[1], [])
        self.assertEqual(parse_similarities(similarities[ids[f'{4:016X}']])[1], [])
        self.assertTrue(all(row['identity_status'] == 'unverified' for row in mapping))

    def test_video_match_rates_have_deduplicated_denominator_across_snapshots(self):
        # An anonymized 65/38/28 fixture exercises the baseline coverage shape.
        groups = [{'video_id': f'fixture-video-{index}',
                   'candidate_pids': [f'fixture-pid-{index}'] if index < 38 else [],
                   'preferred_metadata_pid': f'fixture-pid-{index}' if index < 28 else None}
                  for index in range(65)]
        graph = {'root_pid': 'fixture-root', 'ordered_target_pids': [], 'observations': [{}] * 66,
                 'candidate_groups': groups}
        merged, = merge_graphs([graph, graph])
        self.assertEqual(merged['unique_nonseed_video_count'], 65)
        self.assertEqual(merged['candidate_video_count'], 38)
        self.assertEqual(merged['preferred_metadata_video_count'], 28)
        self.assertEqual(merged['candidate_video_match_rate'], 38 / 65)
        self.assertEqual(merged['preferred_metadata_match_rate'], 28 / 65)
        empty, = merge_graphs([{**graph, 'candidate_groups': []}])
        self.assertIsNone(empty['candidate_video_match_rate'])
        self.assertIsNone(empty['preferred_metadata_match_rate'])

    def test_metrics_and_jaccard_use_generated_pids_and_canonical_artists(self):
        report = evaluate_graphs([self.graph(1, [3, 4]), self.graph(2, [4])], self.matcher,
                                 config(), 'unused', core_factory=FakeCore)
        left, right = report['root_results']
        self.assertEqual(left['generated_count'], 3)
        self.assertEqual(left['unique_artist_count'], 2)
        self.assertEqual(left['adjacent_same_artist_count'], 1)
        self.assertEqual(left['adjacent_same_artist_rate'], 0.5)
        self.assertEqual(left['playlist'][1]['title'], 'Song 3')
        self.assertEqual(right['generated_nonroot_count'], 1)
        self.assertEqual(report['root_overlaps'][0]['candidate_pid_jaccard'], 0.5)
        self.assertEqual(report['root_overlaps'][0]['playlist_pid_jaccard'], 0.25)
        self.assertEqual(report['root_overlaps'][0]['nonroot_playlist_pid_jaccard'], 0.5)
        self.assertEqual(len(FakeCore.instances), 1)
        self.assertEqual(len(FakeCore.instances[0].calls), 2)

    def test_generated_provenance_distinguishes_direct_indirect_and_unreachable(self):
        class ExpandedCore(FakeCore):
            def generate(self, root, limit):
                return {'result_genius_ids': [f'{value:016X}' for value in sorted(self.metadata)]}
        graphs = [self.graph(1, [2]), self.graph(2, [3]), self.graph(4, [])]
        report = evaluate_graphs(graphs, self.matcher, config(), 'unused', core_factory=ExpandedCore)
        result = report['root_results'][0]
        self.assertEqual([track['observed_relation_hops'] for track in result['playlist']], [0, 1, 2, None])
        self.assertEqual(result['direct_candidate_count'], 1)
        self.assertEqual(result['indirect_candidate_count'], 1)
        self.assertEqual(result['unreachable_candidate_count'], 1)

    def test_seed_only_roots_never_instantiate_or_call_core(self):
        def forbidden(*args):
            raise AssertionError('No Core for seed-only graphs')
        report = evaluate_graphs([self.graph(1, [])], self.matcher, config(), 'unused', core_factory=forbidden)
        self.assertEqual(report['root_results'][0]['status'], 'empty_candidates')
        self.assertEqual(report['root_results'][0]['generated_count'], 0)
        self.assertEqual(report['total_instructions'], 0)
        self.assertIsNone(jaccard([], []))

    def test_invalid_schema_search_seed_flags_and_nonfinite_values_are_rejected(self):
        broken = []
        row = self.snapshot(1, [3]); row['observations'][1]['relation'] = 'search_candidate'; broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['is_seed'] = 'false'; broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['is_seed'] = True; broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['position'] = True; broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['observed_at'] = 'no timestamp'; broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['track']['artists'] = ['A']; broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['track']['duration_seconds'] = float('nan'); broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['track']['duration_seconds'] = 10 ** 400; broken.append(row)
        row = self.snapshot(1, [3]); row['observations'][1]['track']['album'] = 'Album'; broken.append(row)
        row = self.snapshot(1, [3]); row['schema_version'] = True; broken.append(row)
        for index, snapshot in enumerate(broken):
            with self.subTest(index=index):
                with self.assertRaises(InvalidObservation):
                    validate_observations(snapshot, 'hash')

    def test_bad_files_skip_safely_and_all_invalid_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid, bad, malformed, missing = [root / name for name in ('valid.json', 'bad.json', 'malformed.json', 'missing.json')]
            valid.write_text(json.dumps(self.snapshot(1, [3])))
            snapshot = self.snapshot(1, [3]); snapshot['observations'][1]['relation'] = 'PRIVATE_EXCEPTION_CONTENT'
            bad.write_text(json.dumps(snapshot))
            malformed.write_text('{secret-exception-content')
            graphs, skipped, inputs = collect_graphs([valid, bad, malformed, missing], self.matcher, 'hash')
            self.assertEqual(len(graphs), 1)
            self.assertEqual(len(skipped), 3)
            self.assertEqual(len(inputs), 3)
            self.assertEqual({row['reason'] for row in skipped}, {'unsupported_observation_source', 'invalid_json', 'unreadable_observation'})
            self.assertNotIn('PRIVATE_EXCEPTION_CONTENT', json.dumps(skipped))
            self.assertNotIn('secret-exception-content', json.dumps(skipped))
            with self.assertRaisesRegex(ValueError, 'No valid'):
                collect_graphs([bad, malformed, missing], self.matcher, 'hash')

    def test_manifest_enumerates_only_declared_pid_files_and_explicit_extras(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'seeds.json').write_text(json.dumps({'library_sha256': 'hash', 'seeds': self.tracks[:2]}))
            paths, digest = observation_paths(root, [root / 'extra.json'], 'hash')
            self.assertEqual([path.name for path in paths], ['0000000000000001.json', '0000000000000002.json', 'extra.json'])
            self.assertEqual(len(digest), 64)
            with self.assertRaisesRegex(ValueError, 'foreign'):
                observation_paths(root, [], 'other-hash')

    @unittest.skipUnless(EXECUTABLE, 'Set OPENGENIUS_MUSIC_EXECUTABLE for binary integration tests')
    def test_actual_core_handles_64_roots_in_one_256_node_space(self):
        # Deliberately synthetic in-memory stress fixture, never saved as recommendations.
        tracks = [{'persistent_id': f'{index:016X}', 'title': f'Stress song {index}', 'artist': f'Artist {index % 8}',
                   'album': f'Album {index % 4}', 'duration_ms': 100000} for index in range(1, 257)]
        matcher = IdentityMap(build_map(tracks, 'stress-fixture'), tracks, 'stress-fixture')
        graphs = []
        for root in range(1, 65):
            target_numbers = [65 + (root - 1) * 3 + offset for offset in range(3)]
            target_numbers.append(65 + (root % 64) * 3)
            target_pids = [f'{number:016X}' for number in target_numbers]
            groups = [{'video_id': f'fixture-{pid}', 'candidate_pids': [pid]} for pid in target_pids]
            graphs.append({'root_pid': f'{root:016X}', 'ordered_target_pids': target_pids,
                           'observations': [{}] * 5, 'candidate_groups': groups})
        report = evaluate_graphs(graphs, matcher, config(), EXECUTABLE)
        self.assertEqual(report['shared_metadata_count'], 256)
        self.assertEqual(len(report['root_results']), 64)
        self.assertEqual(len(report['root_overlaps']), 64 * 63 // 2)
        for root in report['root_results']:
            self.assertEqual(root['playlist_pids'], [root['root_pid'], *root['ordered_target_pids']])
            self.assertEqual(root['identity_status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
