"""Offline typed catalog graph provenance and strict source matching tests."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog_album_graphs import (build_catalog_album_graphs,
    _local_catalog_edges)
from discover_album_catalog_seeds import (build_album_catalog_evidence,
    build_album_plan)
from music_identity_map import IdentityMap, build_map
from plan_genius_update import make_synthetic_state

ALBUM_ID = 'MPREknownalbum01'


def track(n, title, *, artist='Artist', album='Album', duration=100000):
    return {'persistent_id': f'{n:016X}', 'title': title, 'artist': artist,
            'album': album, 'duration_ms': duration}


class CatalogAlbumGraphsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tracks = [track(1, 'Root'), track(2, 'Two'), track(3, 'Three'),
                       track(4, 'Four'), track(5, 'Song (Off Vocal)'),
                       track(6, 'Song (Vocal)')]
        self.library_hash = 'a' * 64
        self.map_doc = build_map(self.tracks, self.library_hash)
        self.map_raw = json.dumps(self.map_doc, ensure_ascii=False).encode()
        self.map_hash = hashlib.sha256(self.map_raw).hexdigest()
        self.matcher = IdentityMap(self.map_doc, self.tracks, self.library_hash)
        self.radio_path = self.root / 'radio.json'
        radio = {'schema_version': 1, 'library_sha256': self.library_hash,
                 'identity_map_sha256': self.map_hash, 'selected_video_id': 'rootvid0001',
                 'observations': [{'source': 'ytmusic', 'relation': 'radio',
                    'is_seed': True, 'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
                    'track': {'video_id': 'rootvid0001', 'title': 'Root',
                      'artists': [{'name': 'Artist'}],
                      'album': {'id': ALBUM_ID, 'name': 'Album'},
                      'duration_seconds': 100}}]}
        self.radio_path.write_text(json.dumps(radio), encoding='utf-8')
        self.plan = build_album_plan(self.tracks, self.library_hash, self.map_hash,
            {'0000000000000001'}, [self.radio_path], self.matcher)
        self.plan_path = self.root / 'plan.json'
        self.plan_path.write_text(json.dumps(self.plan), encoding='utf-8')
        self.response_path = self.root / 'album.json'
        response = {'title': 'Album', 'tracks': [
            {'videoId': f'video00000{i}', 'title': title,
             'artists': [{'name': 'Artist'}], 'album': 'Album',
             'duration_seconds': 100}
            for i, title in enumerate(['Two', 'Three', 'Four'], 1)]}
        response_doc = {'endpoint': 'get_album', 'arguments': {'browseId': ALBUM_ID},
                        'observed_at': '2026-10-09T00:00:00+00:00',
                        'response': response}
        response_raw = json.dumps(response_doc).encode()
        self.response_path.write_bytes(response_raw)
        self.response = {'album_id': ALBUM_ID, 'response': response,
                         'path': str(self.response_path.resolve()),
                         'sha256': hashlib.sha256(response_raw).hexdigest()}
        self.evidence = build_album_catalog_evidence(self.plan, [self.response],
            self.tracks, self.matcher, self.library_hash, self.map_hash)
        self.evidence['album_plan_sha256'] = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        self.evidence_path = self.root / 'evidence.json'
        self.evidence_path.write_text(json.dumps(self.evidence), encoding='utf-8')
        self.state = make_synthetic_state([
            {'root_pid': '0000000000000001', 'ordered_target_pids': ['0000000000000002']},
            {'root_pid': '0000000000000002', 'ordered_target_pids': []}],
            self.matcher, self.library_hash, self.map_hash)
        self.state_path = self.root / 'state.json'
        self.state_path.write_text(json.dumps(self.state), encoding='utf-8')
        self.accepted = {'accepted_roots': [
            {'root_pid': '0000000000000001', 'ordered_target_pids': ['0000000000000002']},
            {'root_pid': '0000000000000002', 'ordered_target_pids': []}]}
        self.accepted_path = self.root / 'accepted.json'
        self.accepted_path.write_text(json.dumps(self.accepted), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def build(self, **kwargs):
        return build_catalog_album_graphs(album_plan=self.plan,
            album_plan_path=self.plan_path, catalog_evidence=self.evidence,
            catalog_evidence_path=self.evidence_path,
            baseline_accepted_graphs=self.accepted,
            baseline_accepted_graphs_path=self.accepted_path,
            baseline_state=self.state, baseline_state_path=self.state_path,
            tracks=self.tracks, matcher=self.matcher,
            library_hash=self.library_hash, identity_map_hash=self.map_hash, **kwargs)

    def test_radio_first_then_catalog_edges_and_source_types_are_separate(self):
        result = self.build(local_catalog=True, track_snapshot_sha256='c' * 64)
        roots = {row['root_pid']: row['ordered_target_pids']
                 for row in result['accepted_graphs']['accepted_roots']}
        self.assertEqual(roots['0000000000000001'][0], '0000000000000002')
        self.assertIn('0000000000000001', roots['0000000000000002'])
        self.assertIn('0000000000000003', roots['0000000000000002'])
        self.assertEqual(result['audit']['radio_observations_created'], 0)
        typed = [(row['root_pid'], row['target_pid'], row['source_relation'])
                 for row in result['provenance']['edge_provenance']]
        self.assertIn(('0000000000000001', '0000000000000002', 'radio'), typed)
        self.assertIn(('0000000000000002', '0000000000000003', 'catalog_index'), typed)
        local = next(row for row in result['provenance']['edge_provenance']
                     if row['source_relation'] == 'local_album_catalog')
        source, = local['sources']
        self.assertEqual(source['source_library_sha256'], self.library_hash)
        self.assertEqual(source['source_track_snapshot_sha256'], 'c' * 64)
        self.assertEqual(len(source['root_track_record_sha256']), 64)
        self.assertEqual(len(source['target_track_record_sha256']), 64)
        self.assertEqual(result['accepted_graphs']['local_album_catalog_graphs'][0]['source_relation'],
                         'local_album_catalog')
        self.assertGreater(len(result['state']['rows']), len(self.state['rows']))

    def test_local_version_facets_keep_off_vocal_and_vocal_separate(self):
        edges, sources, _ = _local_catalog_edges(self.tracks, self.matcher,
            self.library_hash, 'd' * 64, 100)
        off = '0000000000000005'
        vocal = '0000000000000006'
        self.assertNotIn(vocal, edges.get(off, []))
        self.assertNotIn(off, edges.get(vocal, []))
        extra = self.tracks + [track(7, 'Theme BGM'), track(8, 'Theme Vocal'),
                               track(9, 'Theme')]
        matcher = IdentityMap(build_map(extra, self.library_hash), extra, self.library_hash)
        edges, _, _ = _local_catalog_edges(extra, matcher, self.library_hash,
                                            'd' * 64, 100)
        self.assertNotIn('0000000000000008', edges.get('0000000000000007', []))
        self.assertNotIn('0000000000000009', edges.get('0000000000000007', []))
        self.assertTrue(sources)

    def test_local_catalog_rejects_blank_artist_and_nonpositive_duration(self):
        extra = self.tracks + [track(10, 'Ghost', artist=' '),
                               track(11, 'No duration', duration=0)]
        matcher = IdentityMap(build_map(extra, self.library_hash), extra, self.library_hash)
        edges, _, _ = _local_catalog_edges(extra, matcher, self.library_hash,
                                            'e' * 64, 100)
        self.assertNotIn('000000000000000A', edges)
        self.assertNotIn('000000000000000B', edges)
        self.assertFalse(any('000000000000000A' in targets or
                             '000000000000000B' in targets
                             for targets in edges.values()))

    def test_local_catalog_separates_unknown_version_qualifiers(self):
        extra = self.tracks + [track(20, 'Theme (Acoustic)'),
                               track(21, 'Other (Acoustic)'),
                               track(22, 'Theme (BGM)'), track(23, 'Plain Theme')]
        matcher = IdentityMap(build_map(extra, self.library_hash), extra, self.library_hash)
        edges, sources, _ = _local_catalog_edges(extra, matcher, self.library_hash,
                                                  'f' * 64, 100)
        acoustic = '0000000000000014'
        other_acoustic = '0000000000000015'
        bgm = '0000000000000016'
        plain = '0000000000000017'
        self.assertIn(other_acoustic, edges.get(acoustic, []))
        self.assertNotIn(bgm, edges.get(acoustic, []))
        self.assertNotIn(plain, edges.get(acoustic, []))
        profile = sources[(acoustic, other_acoustic)][0]['version_facet_profile']
        self.assertEqual(profile['unknown_qualifiers'], ['acoustic'])

    def test_saved_catalog_response_hash_is_enforced(self):
        self.response_path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.build()


if __name__ == '__main__':
    unittest.main()
