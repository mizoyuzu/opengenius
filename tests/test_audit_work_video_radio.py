from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import audit_work_video_radio as audit
from scripts.collect_work_video_radio import run_collection
from scripts.work_track_video_bindings import (
    LOCAL_ALBUM, UPLOADER_CHANNEL_ID, WORK_LABEL, bind_video_row,
)
from scripts import work_track_video_bindings as binder


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value) -> str:
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(',', ':')).encode())


class AuditWorkVideoRadioTests(unittest.TestCase):
    def setUp(self):
        self.official = {
            '1-06': 'グリーングリーティング',
            '1-07': 'グリーングリーンズ',
            '1-08': 'フレンズフィールド',
        }
        self.local = [
            {'persistent_id': 'D3C7563DDBCF65FB', 'title': 'グリーングリーティング',
             'artist': '安藤浩和', 'album': LOCAL_ALBUM, 'duration_ms': 310066},
            {'persistent_id': 'AAAABBBBCCCCDDDD', 'title': 'グリーングリーンズ',
             'artist': '安藤浩和', 'album': LOCAL_ALBUM, 'duration_ms': 110000},
            {'persistent_id': '1111222233334444', 'title': 'フレンズフィールド',
             'artist': '安藤浩和', 'album': LOCAL_ALBUM, 'duration_ms': 175000},
        ]
        self.rows = {
            'a': self.row('I3yuHmT_DAE', '1-06', 'Green Gardens', '5:12'),
            'b': self.row('CsY_X4Roxz8', '1-07', 'Green Greens', '1:50'),
            'c': self.row('M1eY8pKDhD0', '1-08', 'Friendly Field', '2:55'),
            'b2': self.row('O9VnT9OV_dc', '1-07', 'Green Greens', '1:50'),
        }

    @staticmethod
    def row(video, position, english, length):
        return {'videoId': video,
                'title': f'{position}. {english} - {WORK_LABEL}',
                'artists': [{'name': 'Eternal', 'id': UPLOADER_CHANNEL_ID}],
                'album': None, 'length': length}

    def packet_row(self, row):
        bound = bind_video_row(row, self.official, self.local)
        self.assertIsNotNone(bound)
        return {**bound, 'local_track': {'persistent_id': bound['persistent_id']}}

    def build_stage(self, *, roots=('a',), skip=(), radio_rows=None, max_seeds=44):
        tmp = tempfile.TemporaryDirectory()
        stage = Path(tmp.name) / 'stage'
        stage.mkdir(); (stage / 'requests').mkdir()
        initial = [self.packet_row(self.rows[key]) for key in roots]
        bindings_doc = {'bindings': initial}
        input_hashes = {'fixture_source': {'path': 'fixture', 'sha256': 'source'}}

        class Session:
            def __init__(self):
                self.count = 1
                self.request_categories = Counter({'account_preflight': 1})
                self.request_events = [{
                    'request_number': 1, 'category': 'account_preflight',
                    'status': 'completed', 'method': 'POST',
                    'path': '/youtubei/v1/account/account_menu', 'status_code': 200}]

        session = Session()
        calls = []

        class Client:
            def get_watch_playlist(inner, **kwargs):
                calls.append(dict(kwargs))
                session.count += 1
                category = 'radio_page' if kwargs['radio'] else 'watch_seed_page'
                session.request_categories[category] += 1
                session.request_events.append({
                    'request_number': session.count, 'category': category,
                    'status': 'completed', 'path': '/next'})
                if kwargs['radio']:
                    rows = (radio_rows or {}).get(kwargs['videoId'])
                    if rows is None:
                        rows = [self.rows['a'] if kwargs['videoId'] == self.rows['a']['videoId']
                                else self.rows['c']]
                        extra = self.rows['b'] if kwargs['videoId'] == self.rows['a']['videoId'] else self.rows['b']
                        rows = rows + [extra]
                    return {'tracks': rows}
                return {'tracks': [self.rows['a'] if kwargs['videoId'] == self.rows['a']['videoId']
                                   else self.rows['c']]}

        graph = run_collection(
            bindings_doc=bindings_doc, binding_report={}, official=self.official,
            local_tracks=self.local, input_hashes=input_hashes,
            client=Client(), session=session, output=stage, max_seeds=max_seeds,
            radio_candidate_limit=100, skip_seed_pids=set(skip))
        graph['started_at'] = '2026-10-15T00:00:00+00:00'
        graph['finished_at'] = '2026-10-15T00:01:00+00:00'
        graph_path = stage / 'candidate-graphs.json'
        graph_path.write_text(json.dumps(graph, ensure_ascii=False, sort_keys=True))
        manifest = []
        for path in sorted((stage / 'requests').glob('*.json')):
            raw = path.read_bytes()
            manifest.append({'path': str(path.relative_to(stage)),
                             'sha256': _sha(raw), 'bytes': len(raw)})
        process = {
            'status': 'completed', 'account_authentication_verified': True,
            'http_requests': session.count,
            'http_request_categories': dict(sorted(session.request_categories.items())),
            'request_events': session.request_events,
            'request_cache_manifest': manifest,
            'request_cache_manifest_sha256': _canonical_sha(manifest),
            'request_cache_file_count': len(manifest),
            'inputs': input_hashes,
        }
        progress = {'status': 'completed_finalized_candidate_only',
                    'candidate_graphs': [],
                    'candidate_graphs_sha256': _sha(graph_path.read_bytes())}
        graph['progress_fixture'] = progress
        graph['process_fixture'] = process
        return tmp, stage, graph, process, calls

    def validate_fixture(self, stage, graph, process, *, doc=None,
                         skip=(), frontier=(), base_inputs=None):
        return audit.validate_stage_replay(
            stage, graph=graph, process=process,
            doc=doc or {'bindings': [self.packet_row(self.rows['a'])]},
            official=self.official, local_tracks=self.local,
            frontier_bindings=list(frontier), skip_seed_pids=set(skip),
            base_inputs=base_inputs or process['inputs'], binder=binder,
            expected_seed_limit=graph.get('request_limits', {}).get('seed_max', 44),
            expected_radio_limit=100)

    def test_valid_replay_uses_per_request100_and_seed25_even_when_api_overreturns148(self):
        rows = [self.rows['a'], self.rows['b']]
        for index in range(146):
            rows.append({'videoId': f'bad{index:08d}', 'title': 'unrelated track',
                         'artists': [{'name': 'someone', 'id': 'other'}],
                         'album': None, 'length': '1:00'})
        tmp, stage, graph, process, calls = self.build_stage(
            radio_rows={self.rows['a']['videoId']: rows}, max_seeds=1)
        try:
            result = self.validate_fixture(stage, graph, process)
            self.assertEqual(result['validated_roots'], 1)
            self.assertEqual(result['radio_rows_per_pair'], [148])
            self.assertEqual(calls, [
                {'videoId': self.rows['a']['videoId'], 'radio': True, 'limit': 100},
                {'videoId': self.rows['a']['videoId'], 'radio': False, 'limit': 25},
            ])
        finally:
            tmp.cleanup()

    def test_skipped_seed_pid_remains_a_valid_returned_target(self):
        root_b = binder.bind_rows([self.rows['b']], self.official, self.local)[0][0]
        root_b['frontier_hint'] = {'path': 'fixture/request.json', 'sha256': 'hint',
                                   'response_position': 0,
                                   'metadata_hint_only': True,
                                   'authentication_carried_forward': False,
                                   'credit': 0}
        doc = {'bindings': [self.packet_row(self.rows['a'])]}
        # The second source PID is supplied as a frontier binding while the
        # first PID is skipped as a seed; it remains in B's radio result.
        tmp2 = tempfile.TemporaryDirectory()
        stage2 = Path(tmp2.name) / 'stage'; stage2.mkdir(); (stage2 / 'requests').mkdir()
        session = type('Session', (), {'count': 1,
            'request_categories': Counter({'account_preflight': 1}),
            'request_events': [{'request_number': 1, 'category': 'account_preflight',
                                'status': 'completed', 'method': 'POST',
                                'path': '/youtubei/v1/account/account_menu',
                                'status_code': 200}]})()
        calls2 = []
        class Client:
            def get_watch_playlist(inner, **kwargs):
                calls2.append(dict(kwargs)); session.count += 1
                cat = 'radio_page' if kwargs['radio'] else 'watch_seed_page'
                session.request_categories[cat] += 1
                session.request_events.append({'request_number': session.count,
                    'category': cat, 'status': 'completed', 'path': '/next'})
                if kwargs['radio']:
                    return {'tracks': [self.rows['b'], self.rows['a'], self.rows['c']]}
                returned = next(row for row in self.rows.values()
                                if row['videoId'] == kwargs['videoId'])
                return {'tracks': [returned]}
        input_hashes = {'fixture_source': {'path': 'fixture', 'sha256': 'source'},
                        'frontier_watch_cache_hints': [],
                        'skip_seed_pids': {'path': 'fixture-skip', 'sha256': 'fixture'}}
        graph2 = run_collection(bindings_doc=doc, binding_report={},
            official=self.official, local_tracks=self.local, input_hashes=input_hashes,
            client=Client(), session=session, output=stage2, max_seeds=1,
            radio_candidate_limit=100, frontier_bindings=[root_b],
            skip_seed_pids={'D3C7563DDBCF65FB'})
        manifest = []
        for path in sorted((stage2 / 'requests').glob('*.json')):
            raw = path.read_bytes()
            manifest.append({'path': str(path.relative_to(stage2)), 'sha256': _sha(raw),
                             'bytes': len(raw)})
        process2 = {'status': 'completed', 'account_authentication_verified': True,
            'http_requests': session.count,
            'http_request_categories': dict(sorted(session.request_categories.items())),
            'request_events': session.request_events, 'request_cache_manifest': manifest,
            'request_cache_manifest_sha256': _canonical_sha(manifest),
            'request_cache_file_count': len(manifest), 'inputs': input_hashes}
        try:
            result = audit.validate_stage_replay(stage2, graph=graph2, process=process2,
                doc=doc, official=self.official, local_tracks=self.local,
                frontier_bindings=[root_b], skip_seed_pids={'D3C7563DDBCF65FB'},
                base_inputs={'fixture_source': input_hashes['fixture_source']},
                binder=binder,
                expected_seed_limit=graph2['request_limits']['seed_max'])
            self.assertEqual(result['validated_seed_pairs'], 1)
            self.assertEqual(graph2['candidate_graphs'][0]['persistent_id'],
                             'AAAABBBBCCCCDDDD')
            self.assertEqual(graph2['candidate_graphs'][0]['ordered_target_pids'],
                             ['D3C7563DDBCF65FB', '1111222233334444'])
            self.assertEqual(calls2[0]['limit'], 100)
            self.assertEqual(calls2[1]['limit'], 25)
        finally:
            tmp2.cleanup()

    def test_tampered_ordered_target_is_rejected(self):
        tmp, stage, graph, process, _ = self.build_stage()
        try:
            graph['candidate_graphs'][0]['ordered_target_pids'] = []
            with self.assertRaises(audit.AuditError):
                self.validate_fixture(stage, graph, process)
        finally:
            tmp.cleanup()

    def test_incoming_only_root_is_rejected(self):
        tmp, stage, graph, process, _ = self.build_stage()
        try:
            graph['candidate_graphs'].append({'persistent_id': '1111222233334444',
                'seed_video_id': self.rows['c']['videoId'], 'ordered_target_pids': [],
                'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
                'recording_identity_verified': False})
            with self.assertRaises(audit.AuditError):
                self.validate_fixture(stage, graph, process)
        finally:
            tmp.cleanup()

    def test_missing_own_pair_receipt_is_rejected(self):
        tmp, stage, graph, process, _ = self.build_stage()
        try:
            graph['results'][0]['watch_seed']['receipt_sha256'] = '0' * 64
            with self.assertRaises(audit.AuditError):
                self.validate_fixture(stage, graph, process)
        finally:
            tmp.cleanup()

    def test_auth_false_is_rejected(self):
        tmp, stage, graph, process, _ = self.build_stage()
        try:
            process['account_authentication_verified'] = False
            with self.assertRaises(audit.AuditError):
                self.validate_fixture(stage, graph, process)
        finally:
            tmp.cleanup()

    def test_radio_receipt_for_wrong_seed_is_rejected(self):
        tmp, stage, graph, process, _ = self.build_stage()
        try:
            result = graph['results'][0]
            key = result['watch_radio']['request_key']
            path = stage / 'requests' / (key + '.json')
            request = json.loads(path.read_text())
            request['arguments']['videoId'] = self.rows['b']['videoId']
            raw = json.dumps(request, sort_keys=True, ensure_ascii=False, indent=2).encode()
            path.write_bytes(raw)
            digest = _sha(raw)
            result['watch_radio']['receipt_sha256'] = digest
            for row in process['request_cache_manifest']:
                if row['path'] == 'requests/' + path.name:
                    row['sha256'] = digest; row['bytes'] = len(raw)
            process['request_cache_manifest_sha256'] = _canonical_sha(
                process['request_cache_manifest'])
            with self.assertRaises(audit.AuditError):
                self.validate_fixture(stage, graph, process)
        finally:
            tmp.cleanup()

    def test_altered_source_hash_is_rejected(self):
        tmp, stage, graph, process, _ = self.build_stage()
        try:
            graph['inputs']['fixture_source']['sha256'] = 'altered'
            process['inputs'] = graph['inputs']
            with self.assertRaises(audit.AuditError):
                self.validate_fixture(stage, graph, process,
                    base_inputs={'fixture_source': {'path': 'fixture', 'sha256': 'source'}})
        finally:
            tmp.cleanup()

    def test_global_collision_cannot_be_reintroduced_as_an_edge(self):
        tmp, stage, graph, process, _ = self.build_stage(
            roots=('a', 'c'),
            radio_rows={
                self.rows['a']['videoId']: [self.rows['a'], self.rows['b']],
                self.rows['c']['videoId']: [self.rows['c'], self.rows['b2']],
            })
        try:
            self.assertEqual(graph['candidate_graphs'], [])
            self.assertIn('AAAABBBBCCCCDDDD', graph['summary']['ambiguous_pids'])
            graph['results'][0]['watch_radio']['target_bindings'] = [
                binder.bind_video_row(self.rows['b'], self.official, self.local)]
            graph['results'][0]['watch_radio']['ordered_target_pid_vector'] = [
                'AAAABBBBCCCCDDDD']
            with self.assertRaises(audit.AuditError):
                self.validate_fixture(stage, graph, process,
                    doc={'bindings': [self.packet_row(self.rows['a']),
                                      self.packet_row(self.rows['c'])]})
        finally:
            tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
