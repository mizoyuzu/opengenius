"""Offline proof checks for cache-only alias-map seed hint revalidation."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap
from revalidate_cached_seed_hints import revalidate_cached_seed_hints, _search_cache_path
from seed_video_evidence import strict_cached_search_match


class CachedSeedHintRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pid = '0123456789ABCDEF'
        self.video_id = 'ABCDEFGHIJK'
        self.library_hash = 'a' * 64
        self.tracks = [{'persistent_id': self.pid, 'title': 'Blue Song',
                        'artist': 'Alice', 'album': 'Album', 'duration_ms': 120000}]
        self.snapshot = self.root / 'track-snapshot.json'
        self.snapshot.write_text(json.dumps({'schema_version': 1,
                                             'source_sha256': self.library_hash,
                                             'tracks': self.tracks}), encoding='utf-8')
        self.old_map = self.root / 'old-map.json'
        self.current_map = self.root / 'current-map.json'
        self.old_map.write_text(json.dumps({'schema_version': 1,
                                            'library_sha256': self.library_hash}), encoding='utf-8')
        self.current_map.write_text(json.dumps({
            'schema_version': 1,
            'library_sha256': self.library_hash,
            'artist_credit_aliases': [{
                'remote_credit': 'Alice Roman', 'local_credit': 'Alice',
                'enabled': True,
            }],
        }), encoding='utf-8')
        self.transition_files = {}
        for name in ('graph_export', 'rebase_report', 'state'):
            path = self.root / (name + '.json')
            path.write_text(json.dumps({'fixture': name}), encoding='utf-8')
            self.transition_files[name] = path
        self.transition = {
            'old_identity_map': self._receipt(self.old_map),
            'new_identity_map': self._receipt(self.current_map),
            'graph_export': self._receipt(self.transition_files['graph_export']),
            'rebase_report': self._receipt(self.transition_files['rebase_report']),
            'state': self._receipt(self.transition_files['state']),
        }
        self.collector = self.root / 'collector'
        (self.collector / 'requests').mkdir(parents=True)
        self.arguments = {'query': 'Blue Song Alice', 'filter': 'songs', 'limit': 5}
        self.response_path = _search_cache_path(self.collector, self.arguments)
        self.response_path.parent.mkdir(parents=True, exist_ok=True)
        self.response_path.write_text(json.dumps({
            'endpoint': 'search', 'arguments': self.arguments,
            'observed_at': '2026-10-09T00:00:00+00:00',
            'response': [{'videoId': self.video_id, 'title': 'Blue Song',
                          'artists': [{'name': 'Alice Roman'}],
                          'duration_seconds': 120,
                          'album': {'name': 'Album'}}],
        }), encoding='utf-8')
        self.manifest = self.collector / 'seeds.json'
        self.manifest.write_text(json.dumps({
            'library_sha256': self.library_hash,
            'identity_map_sha256': hashlib.sha256(self.old_map.read_bytes()).hexdigest(),
            'seeds': [{'persistent_id': self.pid, 'title': 'Blue Song',
                       'artist': 'Alice', 'album': 'Album',
                       'duration_ms': 120000}],
        }), encoding='utf-8')
        self.summary = self.collector / 'summary-1.json'
        self.summary.write_text(json.dumps({
            'status': 'completed', 'http_requests': 1, 'request_budget': 2,
            'library_sha256': self.library_hash,
            'account_authentication_verified': True,
            'results': [{'persistent_id': self.pid, 'status': 'search_unresolved',
                         'candidate_video_ids': 0,
                         'title_fallback_attempted': False}],
        }), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def run_revalidation(self):
        old_hash = hashlib.sha256(self.old_map.read_bytes()).hexdigest()
        current_hash = hashlib.sha256(self.current_map.read_bytes()).hexdigest()
        verified = {'old_identity_map_sha256': old_hash,
                    'new_identity_map_sha256': current_hash,
                    'library_sha256': self.library_hash,
                    'accepted_snapshot_receipts': []}
        with patch('revalidate_cached_seed_hints.verify_typed_map_transition',
                   return_value=(self.transition, verified)):
            return revalidate_cached_seed_hints(
                track_snapshot=self.snapshot,
                old_identity_map=self.old_map,
                current_identity_map=self.current_map,
                collector_dirs=[self.collector],
                typed_transition=self.transition)

    @staticmethod
    def _receipt(path):
        return {'path': str(path.resolve()),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_current_alias_match_yields_only_a_hash_bound_metadata_hint(self):
        report = self.run_revalidation()
        self.assertEqual(len(report['rows']), 1)
        row = report['rows'][0]
        self.assertEqual(row['persistent_id'], self.pid)
        self.assertEqual(row['video_id'], self.video_id)
        self.assertEqual(row['source_relation'], 'cached_search_candidate_unverified')
        self.assertNotEqual(report['prior_identity_map']['sha256'],
                            report['revalidated_identity_map']['sha256'])
        self.assertEqual(row['historical_old_identity_map_sha256'],
                         report['prior_identity_map']['sha256'])
        self.assertEqual(row['revalidated_identity_map_sha256'],
                         report['revalidated_identity_map']['sha256'])
        self.assertEqual(report['audit']['recommendation_observations_created'], 0)
        self.assertEqual(report['audit']['recommendation_edges_created'], 0)
        self.assertEqual(report['audit']['outgoing_roots_created'], 0)
        self.assertEqual(report['audit']['network_requests'], 0)

    def test_cache_argument_mismatch_cannot_mint_a_hint(self):
        envelope = json.loads(self.response_path.read_text(encoding='utf-8'))
        envelope['arguments']['query'] = 'different query'
        self.response_path.write_text(json.dumps(envelope), encoding='utf-8')
        report = self.run_revalidation()
        self.assertEqual(report['rows'], [])
        self.assertIn('cached_search_envelope_mismatch',
                      {row['reason'] for row in report['skipped']})

    def test_manifest_must_bind_the_actual_old_map(self):
        manifest = json.loads(self.manifest.read_text(encoding='utf-8'))
        manifest['identity_map_sha256'] = 'f' * 64
        self.manifest.write_text(json.dumps(manifest), encoding='utf-8')
        report = self.run_revalidation()
        self.assertEqual(report['rows'], [])
        self.assertIn('manifest_library_or_old_map_mismatch',
                      {row['reason'] for row in report['skipped']})

    def test_legacy_summary_without_fallback_flag_requires_no_cached_fallback_request(self):
        summary = json.loads(self.summary.read_text(encoding='utf-8'))
        del summary['results'][0]['title_fallback_attempted']
        self.summary.write_text(json.dumps(summary), encoding='utf-8')
        report = self.run_revalidation()
        self.assertEqual(len(report['rows']), 1)
        self.assertEqual(report['rows'][0]['historical_fallback_status'],
                         'legacy_summary_omits_flag_no_fallback_cache')
        fallback = {'query': 'Blue Song', 'filter': 'songs', 'limit': 5}
        fallback_path = _search_cache_path(self.collector, fallback)
        fallback_path.write_text(json.dumps({
            'endpoint': 'search', 'arguments': fallback,
            'observed_at': '2026-10-09T00:01:00+00:00', 'response': []}),
            encoding='utf-8')
        report = self.run_revalidation()
        self.assertEqual(report['rows'], [])
        self.assertIn('fallback_history_flag_missing_or_cached_fallback_exists',
                      {row['reason'] for row in report['skipped']})

    def test_strict_resolver_requires_album_title_version_and_duration_support(self):
        tracks = json.loads(self.snapshot.read_text(encoding='utf-8'))['tracks']
        matcher = IdentityMap(json.loads(self.current_map.read_text(encoding='utf-8')),
                              tracks, self.library_hash)
        base = {'videoId': self.video_id, 'title': 'Blue Song',
                'artists': [{'name': 'Alice Roman'}], 'duration_seconds': 120,
                'album': {'name': 'Album'}}
        matched = strict_cached_search_match([base], self.manifest_seed(), matcher)
        self.assertEqual(matched['video_id'], self.video_id)
        for change in (
                {'album': None},
                {'title': 'Blue Song (Instrumental)'},
                {'duration_seconds': 124},
                {'artists': [{'name': 'Other Artist'}]}):
            candidate = dict(base)
            candidate.update(change)
            with self.subTest(change=change):
                self.assertIsNone(strict_cached_search_match(
                    [candidate], self.manifest_seed(), matcher))

    def test_strict_resolver_rejects_multiple_matching_video_ids(self):
        tracks = json.loads(self.snapshot.read_text(encoding='utf-8'))['tracks']
        matcher = IdentityMap(json.loads(self.current_map.read_text(encoding='utf-8')),
                              tracks, self.library_hash)
        first = {'videoId': self.video_id, 'title': 'Blue Song',
                 'artists': [{'name': 'Alice Roman'}], 'duration_seconds': 120,
                 'album': {'name': 'Album'}}
        second = dict(first, videoId='LMNOPQRSTUV')
        self.assertIsNone(strict_cached_search_match(
            [first, second], self.manifest_seed(), matcher))

    def manifest_seed(self):
        return {'persistent_id': self.pid, 'title': 'Blue Song',
                'artist': 'Alice', 'album': 'Album', 'duration_ms': 120000}


if __name__ == '__main__':
    unittest.main()
