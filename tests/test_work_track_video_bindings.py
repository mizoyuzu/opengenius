from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from collections import Counter

from scripts.work_track_video_bindings import (
    LOCAL_ALBUM, POLICY, UPLOADER_CHANNEL_ID, WORK_LABEL,
    bind_rows, bind_video_row, ordered_radio_targets, parse_official_tracklist,
    parse_work_title,
)
from scripts.collect_work_video_radio import (initial_frontier, mark_stopped,
                                              request_key, run_collection)
from scripts.work_track_video_bindings import load_binding_inputs
from scripts.work_track_video_bindings import load_frontier_watch_cache, load_skip_seed_pids


class WorkTrackVideoBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[1]
        cls.official = {'1-06': 'グリーングリーティング',
                        '1-07': 'グリーングリーンズ',
                        '1-08': 'フレンズフィールド'}
        cls.local = [
            {'persistent_id': 'D3C7563DDBCF65FB', 'title': 'グリーングリーティング',
             'artist': '安藤浩和', 'album': LOCAL_ALBUM, 'duration_ms': 310066},
            {'persistent_id': 'AAAABBBBCCCCDDDD', 'title': 'グリーングリーンズ',
             'artist': '安藤浩和', 'album': LOCAL_ALBUM, 'duration_ms': 110000},
            {'persistent_id': '1111222233334444', 'title': 'フレンズフィールド',
             'artist': '安藤浩和', 'album': LOCAL_ALBUM, 'duration_ms': 175000},
        ]

    def row(self, video='I3yuHmT_DAE', position='1-06', english='Green Gardens',
            length='5:12', channel=UPLOADER_CHANNEL_ID, work=WORK_LABEL):
        return {'videoId': video,
                'title': f'{position}. {english} - {work}',
                'artists': [{'name': 'Eternal', 'id': channel}],
                'album': None, 'length': length}

    def test_real_saved_hall_html_and_radio_packet_yield_25_contextual_candidates(self):
        root = self.repo / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01'
        html = (root / 'hallab-official-page.html').read_bytes()
        official = parse_official_tracklist(html)
        self.assertEqual(len(official), 233)
        packet = json.loads((root / 'candidate-bindings.json').read_text())
        snapshot = json.loads((self.repo / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json').read_text())
        local = [t for t in snapshot['tracks'] if t.get('album') == LOCAL_ALBUM]
        request_path = self.repo / 'data/ytmusic/post870-video-seed-feasibility-20261015-v01/requests/2a4e6f96a020d7af80566271f886a08c76eaa4029d6150fda1d784b213c16448.json'
        request = json.loads(request_path.read_text())
        rows, stats = bind_rows(request['response']['tracks'], official, local)
        self.assertEqual(len(rows), 25)
        self.assertEqual(stats['unique_strict_bindings'], 25)
        self.assertEqual({x['persistent_id'] for x in rows},
                         {x['local_track']['persistent_id'] for x in packet['bindings']})
        self.assertTrue(all(x['policy'] == POLICY and x['recording_identity_verified'] is False
                            for x in rows))
        loaded = load_binding_inputs(root / 'candidate-bindings.json',
                                     root / 'report.json', root / 'hallab-official-page.html',
                                     self.repo / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json',
                                     repo=self.repo)
        self.assertEqual(len(initial_frontier(loaded[0], loaded[2], loaded[3], 44)), 25)

    def test_exact_ordinal_official_title_and_duration_required(self):
        self.assertEqual(parse_work_title(self.row()['title'])['disc_track'], '1-06')
        self.assertIsNone(parse_work_title('Green Gardens - KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK'))
        self.assertIsNone(parse_work_title('1-06. Green Gardens Extended - ' + WORK_LABEL))
        self.assertIsNone(parse_work_title('1-06. Green Gardens 1-07 - ' + WORK_LABEL))
        self.assertIsNone(parse_work_title('1-06. Green Gardens - kirby star allies: the original soundtrack'))
        self.assertIsNone(bind_video_row(self.row(length='5:15'), self.official, self.local))
        self.assertIsNone(bind_video_row(self.row(position='1-99'), self.official, self.local))

    def test_wrong_channel_work_release_or_version_is_rejected(self):
        self.assertIsNone(bind_video_row(self.row(channel='other-channel'), self.official, self.local))
        self.assertIsNone(bind_video_row(self.row(work='Kirby Star Allies Music'), self.official, self.local))
        self.assertIsNone(bind_video_row(self.row(english='Green Gardens gamerip'), self.official, self.local))
        self.assertIsNone(bind_video_row(self.row(video='bad!'), self.official, self.local))

    def test_local_title_ambiguity_and_nonpositive_duration_are_rejected(self):
        duplicate = self.local + [{**self.local[0], 'persistent_id': 'EEEEFFFFAAAABBBB'}]
        self.assertIsNone(bind_video_row(self.row(), self.official, duplicate))
        zero_duration = [{**self.local[0], 'duration_ms': 0}]
        self.assertIsNone(bind_video_row(self.row(), self.official, zero_duration))

    def test_pid_video_collision_rows_are_removed_from_radio_vector(self):
        rows = [self.row(),
                self.row(video='CsY_X4Roxz8', position='1-06', english='Green Gardens'),
                self.row(video='O9VnT9OV_dc', position='1-07', english='Green Greens', length='1:50')]
        cleaned, stats = bind_rows(rows, self.official, self.local)
        self.assertEqual([x['persistent_id'] for x in cleaned], ['AAAABBBBCCCCDDDD'])
        self.assertEqual(stats['ambiguous_pid_count'], 1)
        targets, target_stats = ordered_radio_targets(rows, 'D3C7563DDBCF65FB', self.official, self.local)
        self.assertEqual([x['persistent_id'] for x in targets], ['AAAABBBBCCCCDDDD'])
        self.assertEqual(target_stats['ordered_unique_target_count'], 1)

    def test_ordered_targets_skip_self_and_dedupe_pid(self):
        rows = [self.row(), self.row(video='CsY_X4Roxz8', position='1-07',
                                     english='Green Greens', length='1:50')]
        targets, stats = ordered_radio_targets(rows, 'D3C7563DDBCF65FB',
                                               self.official, self.local)
        self.assertEqual([x['persistent_id'] for x in targets], ['AAAABBBBCCCCDDDD'])
        self.assertEqual(stats['seed_self_rows_excluded'], 1)

    def test_no_network_roundtrip_requires_seed_return_and_nonempty_radio_targets(self):
        seed = self.row()
        target = self.row(video='CsY_X4Roxz8', position='1-07',
                          english='Green Greens', length='1:50')

        class FakeSession:
            def __init__(self):
                self.count = 0
                self.request_categories = Counter()
                self.request_events = []
                self.limits = []

        session = FakeSession()

        class FakeClient:
            def get_watch_playlist(self, **kwargs):
                session.count += 1
                session.limits.append((kwargs['radio'], kwargs['limit']))
                if kwargs['radio']:
                    return {'tracks': [seed, target]}
                return {'tracks': [seed]}

        binding = bind_video_row(seed, self.official, self.local)
        packet_binding = {**binding,
                          'local_track': {'persistent_id': binding['persistent_id']}}
        bindings_doc = {'bindings': [packet_binding]}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'requests').mkdir()
            result = run_collection(bindings_doc=bindings_doc, binding_report={},
                                    official=self.official, local_tracks=self.local,
                                    input_hashes={'fixture': 'no-network'},
                                    client=FakeClient(), session=session,
                                    output=output, max_seeds=1,
                                    radio_candidate_limit=100)
            progress = json.loads((output / 'progress.json').read_text())
            self.assertEqual(progress['status'], 'collecting_nonterminal_provisional')
            self.assertEqual(progress['root_credit'], 0)
            self.assertEqual(len(list((output / 'requests').glob('*.json'))), 2)
        self.assertEqual(result['summary']['seeds_attempted'], 1)
        self.assertEqual(result['summary']['candidate_graphs_ready'], 1)
        candidate = result['candidate_graphs'][0]
        self.assertEqual(candidate['persistent_id'], 'D3C7563DDBCF65FB')
        self.assertEqual(candidate['ordered_target_pids'], ['AAAABBBBCCCCDDDD'])
        self.assertEqual(candidate['root_credit'], 0)
        self.assertIs(candidate['recording_identity_verified'], False)
        self.assertEqual(session.limits, [(True, 100), (False, 25)])

    def test_no_seed_return_yields_no_candidate_graph(self):
        seed = self.row()
        target = self.row(video='CsY_X4Roxz8', position='1-07',
                          english='Green Greens', length='1:50')

        class FakeSession:
            count = 0
            request_categories = Counter()
            request_events = []

        session = FakeSession()

        class FakeClient:
            def get_watch_playlist(self, **kwargs):
                session.count += 1
                return {'tracks': [seed, target] if kwargs['radio'] else [target]}

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'requests').mkdir()
            binding = bind_video_row(seed, self.official, self.local)
            packet_binding = {**binding,
                              'local_track': {'persistent_id': binding['persistent_id']}}
            result = run_collection(bindings_doc={'bindings': [packet_binding]},
                                    binding_report={}, official=self.official,
                                    local_tracks=self.local, input_hashes={},
                                    client=FakeClient(), session=session,
                                    output=output, max_seeds=1,
                                    radio_candidate_limit=100)
        self.assertEqual(result['candidate_graphs'], [])
        self.assertEqual(result['results'][0]['status'], 'seed_not_returned_or_binding_invalid')

    def test_later_global_pid_video_collision_removes_earlier_target_edge(self):
        seed_a = self.row()
        seed_c = self.row(video='M1eY8pKDhD0', position='1-08',
                          english='Friendly Field', length='2:55')
        target_b1 = self.row(video='CsY_X4Roxz8', position='1-07',
                             english='Green Greens', length='1:50')
        target_b2 = self.row(video='O9VnT9OV_dc', position='1-07',
                             english='Green Greens', length='1:50')

        class FakeSession:
            def __init__(self):
                self.count = 0
                self.request_categories = Counter()
                self.request_events = []

        session = FakeSession()

        class FakeClient:
            def get_watch_playlist(self, **kwargs):
                session.count += 1
                vid = kwargs['videoId']
                if kwargs['radio']:
                    return {'tracks': ([seed_a, target_b1] if vid == seed_a['videoId']
                                       else [seed_c, target_b2])}
                return {'tracks': [seed_a if vid == seed_a['videoId'] else seed_c]}

        def packet(row):
            bound = bind_video_row(row, self.official, self.local)
            return {**bound, 'local_track': {'persistent_id': bound['persistent_id']}}

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'requests').mkdir()
            result = run_collection(bindings_doc={'bindings': [packet(seed_a), packet(seed_c)]},
                                    binding_report={}, official=self.official,
                                    local_tracks=self.local, input_hashes={},
                                    client=FakeClient(), session=session,
                                    output=output, max_seeds=2,
                                    radio_candidate_limit=100)
        # The second independent watch-radio response discovers a conflicting
        # video for the same PID; the earlier vector must be re-filtered.
        self.assertEqual(result['summary']['ambiguous_pids'], ['AAAABBBBCCCCDDDD'])
        self.assertEqual(result['candidate_graphs'], [])
        self.assertTrue(all(not item['watch_radio']['ordered_target_pid_vector']
                            for item in result['results']))

    def test_failure_preserves_completed_pair_as_zero_credit_provisional_progress(self):
        seed_a = self.row()
        seed_c = self.row(video='M1eY8pKDhD0', position='1-08',
                          english='Friendly Field', length='2:55')

        class FakeSession:
            def __init__(self):
                self.count = 0
                self.request_categories = Counter()
                self.request_events = []

        session = FakeSession()

        class FakeClient:
            def get_watch_playlist(self, **kwargs):
                session.count += 1
                if kwargs['radio'] and kwargs['videoId'] == seed_c['videoId']:
                    raise OSError('simulated transport failure')
                if kwargs['radio']:
                    return {'tracks': [seed_a, seed_c]}
                return {'tracks': [seed_a]}

        def packet(row):
            bound = bind_video_row(row, self.official, self.local)
            return {**bound, 'local_track': {'persistent_id': bound['persistent_id']}}

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'requests').mkdir()
            initial = {'status': 'running', 'http_requests': 0}
            with self.assertRaisesRegex(OSError, 'simulated transport failure'):
                run_collection(bindings_doc={'bindings': [packet(seed_a)]},
                               binding_report={}, official=self.official,
                               local_tracks=self.local, input_hashes={},
                               client=FakeClient(), session=session,
                               output=output, max_seeds=2,
                               radio_candidate_limit=100)
            mark_stopped(initial, session, output,
                         OSError('simulated transport failure'), True)
            progress = json.loads((output / 'progress.json').read_text())
        self.assertEqual(initial['status'], 'stopped')
        self.assertEqual(initial['http_requests'], 3)
        self.assertEqual(progress['status'], 'stopped_nonterminal_provisional')
        self.assertEqual(progress['completed_seed_pairs'], 1)
        self.assertEqual(len(progress['results_so_far']), 1)
        self.assertEqual(progress['http_requests'], 3)
        self.assertEqual(progress['root_credit'], 0)
        self.assertEqual(progress['edge_credit'], 0)
        self.assertEqual(progress['source_credit'], 0)

    def test_continuation_skips_old_seed_but_freshly_probes_uncredited_cache_hint(self):
        old_seed = self.row()
        hinted_seed = self.row(video='CsY_X4Roxz8', position='1-07',
                               english='Green Greens', length='1:50')
        new_target = self.row(video='O9VnT9OV_dc', position='1-08',
                              english='Friendly Field', length='2:55')
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            request_dir = folder / 'requests'; request_dir.mkdir()
            hint_path = request_dir / (request_key(old_seed['videoId'], True, 25) + '.json')
            hint_path.write_text(json.dumps({
                'endpoint': 'get_watch_playlist',
                'arguments': {'videoId': old_seed['videoId'], 'radio': True, 'limit': 25},
                'observed_at': '2026-10-15T00:00:00+00:00',
                'response': {'tracks': [old_seed, hinted_seed]},
            }))
            hints, receipt = load_frontier_watch_cache(hint_path, self.official, self.local)
            self.assertEqual(receipt['strict_metadata_candidate_count'], 2)
            self.assertTrue(all(x['frontier_hint']['metadata_hint_only'] for x in hints))
            skip_path = folder / 'skip-pids.json'
            skip_path.write_text(json.dumps(['D3C7563DDBCF65FB']))
            skipped, skip_receipt = load_skip_seed_pids(skip_path, self.local)
            self.assertEqual(skipped, {'D3C7563DDBCF65FB'})
            self.assertEqual(skip_receipt['credit'], 0)

            class FakeSession:
                def __init__(self):
                    self.count = 0
                    self.request_categories = Counter()
                    self.request_events = []
                    self.limits = []

            session = FakeSession()

            class FakeClient:
                def get_watch_playlist(self, **kwargs):
                    session.count += 1
                    session.limits.append((kwargs['radio'], kwargs['limit']))
                    if kwargs['radio']:
                        # A skipped seed remains eligible as a radio target.
                        return {'tracks': [hinted_seed, old_seed, new_target]}
                    return {'tracks': [hinted_seed]}

            old_binding = bind_video_row(old_seed, self.official, self.local)
            base_packet = {**old_binding,
                           'local_track': {'persistent_id': old_binding['persistent_id']}}
            output = folder / 'out'
            output.mkdir(); (output / 'requests').mkdir()
            result = run_collection(bindings_doc={'bindings': [base_packet]},
                                    binding_report={}, official=self.official,
                                    local_tracks=self.local,
                                    input_hashes={'frontier': [receipt], 'skip': skip_receipt},
                                    client=FakeClient(), session=session,
                                    output=output, max_seeds=1,
                                    radio_candidate_limit=100,
                                    frontier_bindings=hints,
                                    skip_seed_pids=skipped)
        self.assertEqual(result['summary']['seeds_attempted'], 1)
        self.assertEqual(result['results'][0]['persistent_id'], 'AAAABBBBCCCCDDDD')
        self.assertEqual(result['results'][0]['status'], 'candidate_ready')
        self.assertEqual(result['candidate_graphs'][0]['ordered_target_pids'],
                         ['D3C7563DDBCF65FB', '1111222233334444'])
        self.assertEqual(result['candidate_graphs'][0]['root_credit'], 0)
        self.assertEqual(session.limits, [(True, 100), (False, 25)])

    def test_frontier_hint_rejects_wrong_endpoint_and_nonradio_call(self):
        row = self.row()
        for endpoint, radio in [('search', True), ('get_watch_playlist', False)]:
            with self.subTest(endpoint=endpoint, radio=radio), tempfile.TemporaryDirectory() as temporary:
                request_dir = Path(temporary) / 'requests'; request_dir.mkdir()
                path = request_dir / 'bad.json'
                path.write_text(json.dumps({
                    'endpoint': endpoint,
                    'arguments': {'videoId': row['videoId'], 'radio': radio, 'limit': 25},
                    'observed_at': '2026-10-15T00:00:00+00:00',
                    'response': {'tracks': [row]},
                }))
                with self.assertRaises(ValueError):
                    load_frontier_watch_cache(path, self.official, self.local)


if __name__ == '__main__':
    unittest.main()
