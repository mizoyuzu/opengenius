"""Mocked transport checks for the paced album-search runner."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map
from search_unindexed_album_sources import build_album_search_plan
from run_album_search_sources import run_album_searches


class FakeSession:
    def __init__(self):
        self.count = 0
        self.closed = False
    def close(self):
        self.closed = True


class RunAlbumSearchSourcesTests(unittest.TestCase):
    def test_runner_paces_auth_and_searches_and_saves_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracks = [
                {'persistent_id': '0000000000000001', 'title': 'Song one',
                 'artist': 'Artist', 'album': 'Album', 'duration_ms': 100000},
                {'persistent_id': '0000000000000002', 'title': 'Song two',
                 'artist': 'Artist', 'album': 'Album', 'duration_ms': 100000},
            ]
            map_doc = build_map(tracks, 'a' * 64)
            map_path = root / 'map.json'
            map_path.write_text(json.dumps(map_doc, ensure_ascii=False))
            map_hash = hashlib.sha256(map_path.read_bytes()).hexdigest()
            matcher = IdentityMap(map_doc, tracks, 'a' * 64)
            plan = build_album_search_plan(tracks, 'a' * 64, map_hash, matcher,
                set(), state_hash='c' * 64, track_snapshot_hash='d' * 64, limit=1)
            plan_path = root / 'plan.json'
            plan_path.write_text(json.dumps(plan, ensure_ascii=False))
            snapshot_path = root / 'tracks.json'
            snapshot_path.write_text(json.dumps({'schema_version': 1,
                'source_sha256': 'a' * 64, 'tracks': tracks}, ensure_ascii=False))
            state_path = root / 'state.json'
            state_path.write_text(json.dumps({'state_sha256': 'c' * 64, 'rows': []}))
            auth_path = root / 'auth.json'
            auth_path.write_text('{"auth":"secret"}')
            session = FakeSession()
            seen = {}
            class Client:
                def __init__(self, *args, **kwargs):
                    seen['auth_arg'] = args[0]
                    seen['session_arg'] = kwargs['requests_session']
                def get_account_info(self):
                    session.count += 1
                    return {'accountName': 'private account'}
                def search(self, **kwargs):
                    session.count += 1
                    seen['query_args'] = kwargs
                    return [{'browseId': 'MPREalbum123', 'title': 'Album',
                        'artists': [{'name': 'Artist'}], 'feedbackTokens': 'secret'}]
            def session_factory(interval, budget, *, deadline):
                seen.update(interval=interval, budget=budget, deadline=deadline)
                return session
            report = run_album_searches(plan_path=plan_path,
                track_snapshot=snapshot_path, identity_map_path=map_path,
                state_path=state_path, auth_path=auth_path,
                output_dir=root / 'out', client_factory=Client,
                session_factory=session_factory)
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['http_requests'], 2)
            self.assertEqual(seen['interval'], 2)
            self.assertEqual(seen['query_args'], {'query': 'Album Artist',
                'filter': 'albums', 'limit': 5})
            self.assertTrue(report['account_authentication_verified'])
            evidence = json.loads((root / 'out/album-search-evidence.json').read_text())
            self.assertEqual(evidence['audit']['candidate_count'], 1)
            response = json.loads((root / 'out/responses/search-00.json').read_text())
            self.assertNotIn('feedbackTokens', json.dumps(response))
            self.assertNotIn('private account', (root / 'out/summary.json').read_text())
            self.assertTrue(session.closed)


if __name__ == '__main__':
    unittest.main()
