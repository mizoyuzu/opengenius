"""Review edits remain Library-bound, transactional and separate from inputs."""
import copy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from serve_music_review import ReviewSession, make_handler


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.snapshot = self.directory / 'tracks.json'
        self.tracks = [{'persistent_id': f'{i:016X}', 'title': title, 'artist': 'Composer',
                        'album': 'Mixed OST', 'duration_ms': 100000}
                       for i, title in enumerate(('Theme', 'Song (Off Vocal)', 'Vocal'), 1)]
        self.snapshot.write_text(json.dumps({'source_sha256': 'a' * 64, 'tracks': self.tracks}))
        self.original_bytes = self.snapshot.read_bytes()
        self.session = ReviewSession(self.snapshot, output_directory=self.directory / 'output')

    def test_album_batch_keeps_tags_and_off_vocal_until_explicit_kind_override(self):
        pids = [t['persistent_id'] for t in self.tracks]
        self.session.edit({'persistent_ids': pids, 'tags': ['Game'], 'tag_mode': 'add'})
        labels = self.session.state()['classifications']
        self.assertEqual(labels[pids[1]]['kind'], 'off_vocal')
        self.assertEqual(labels[pids[0]]['kind'], 'unknown')
        self.session.edit({'persistent_ids': [pids[0]], 'kind': 'bgm'})
        self.session.edit({'persistent_ids': [pids[0]], 'tags': ['Brand'], 'tag_mode': 'add'})
        labels = self.session.state()['classifications']
        self.assertEqual(labels[pids[0]]['tags'], ['Game', 'Brand'])
        self.assertEqual(labels[pids[0]]['kind'], 'bgm')
        self.session.edit({'persistent_ids': [pids[0]], 'reset': True})
        self.assertEqual(self.session.state()['classifications'][pids[0]]['kind'], 'unknown')
        self.assertEqual(self.snapshot.read_bytes(), self.original_bytes)

    def test_invalid_edits_do_not_partially_change_session(self):
        before = copy.deepcopy(self.session.config)
        for edit in ({'persistent_ids': ['0000000000000001', 'FFFFFFFFFFFFFFFF'], 'kind': 'bgm'},
                     {'persistent_ids': ['0000000000000001'], 'kind': 'guess'},
                     {'persistent_ids': ['0000000000000001'], 'tags': ['Tag', 'Ｔａｇ']},
                     {'persistent_ids': ['0000000000000001'], 'reset': True, 'tags': []}):
            with self.assertRaises(ValueError):
                self.session.edit(edit)
            self.assertEqual(self.session.config, before)

    def test_save_creates_new_loadable_configs_and_never_overwrites_snapshot(self):
        self.session.edit({'persistent_ids': ['0000000000000001'], 'kind': 'bgm'})
        self.assertTrue(self.session.state()['has_unsaved_changes'])
        first, second = self.session.save(), self.session.save()
        self.assertNotEqual(first['path'], second['path'])
        self.assertFalse(self.session.state()['has_unsaved_changes'])
        restored = ReviewSession(self.snapshot, config=Path(first['path']))
        self.assertEqual(restored.config, self.session.config)
        self.assertEqual(self.snapshot.read_bytes(), self.original_bytes)

    def test_foreign_evaluation_is_rejected(self):
        report = self.directory / 'foreign.json'
        report.write_text(json.dumps({'library_sha256': 'b' * 64, 'root_results': []}))
        with self.assertRaises(ValueError):
            ReviewSession(self.snapshot, evaluation=report)

    def test_evaluation_scopes_all_edges_and_retains_other_roots_for_two_hops(self):
        pids = [t['persistent_id'] for t in self.tracks]
        self.session.edit({'persistent_ids': pids[:2], 'tags': ['Game'], 'kind': 'bgm'})
        graphs = [{'root_pid': pids[0], 'ordered_target_pids': [pids[1]]},
                  {'root_pid': pids[1], 'ordered_target_pids': [pids[2]]}]
        self.session.engine = (graphs, 'matcher', b'config', 'executable', {'fixture': True})
        with patch('evaluate_ytmusic_batch.evaluate_graphs') as evaluate:
            evaluate.return_value = {'root_results': [{'root_pid': pids[0]}, {'root_pid': pids[1]}],
                                     'root_overlaps': [{}]}
            result = self.session.evaluate({'tags': ['Game'], 'kind': 'bgm', 'root_pid': pids[0]})
        scoped = evaluate.call_args.args[0]
        self.assertEqual(len(scoped), 2)
        self.assertEqual(scoped[1]['ordered_target_pids'], [])
        self.assertEqual(result['evaluation']['root_results'], [{'root_pid': pids[0]}])
        self.assertEqual(result['evaluation']['cluster_config_sha256'], self.session.config_hash())
        self.assertTrue(Path(result['path']).exists())

    def test_http_blocks_cross_origin_and_serves_local_edits(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.session, 'test-token'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = HTTPConnection('127.0.0.1', server.server_port)
        self.addCleanup(conn.close)
        origin = f'http://127.0.0.1:{server.server_port}'
        conn.request('GET', '/api/state')
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(len(json.loads(response.read())['tracks']), 3)
        body = json.dumps({'persistent_ids': ['0000000000000001'], 'kind': 'bgm'})
        headers = {'Content-Type': 'application/json', 'Origin': 'https://other.example', 'X-Review-Token': 'test-token'}
        conn.request('POST', '/api/edit', body, headers)
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 403)
        headers['Origin'] = origin
        conn.request('POST', '/api/edit', body, headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())['classifications']['0000000000000001']['kind'], 'bgm')
        conn.request('GET', '/api/state', headers={'Host': 'other.example'})
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 200)
        headers.update({'Host': 'review.example:8765', 'Origin': 'https://review.example:8765'})
        conn.request('POST', '/api/edit', body, headers)
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 200)
        headers['X-Review-Token'] = 'wrong-token'
        conn.request('POST', '/api/edit', body, headers)
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 403)


if __name__ == '__main__':
    unittest.main()
