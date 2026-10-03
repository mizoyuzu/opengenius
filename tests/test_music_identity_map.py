import copy
import io
import json
import tempfile
from unittest.mock import patch
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, apply_title_review, build_map, load_library, main, version_signature


class IdentityMapTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': 'A', 'title': '曲 (Off Vocal) - Song (Off Vocal)',
             'artist': "L'Antica", 'duration_ms': 100000, 'album': 'Album'},
            {'persistent_id': 'B', 'title': '曲 - Song',
             'artist': "L'Antica", 'duration_ms': 100000, 'album': 'Album'},
        ]
        self.document = build_map(self.tracks, 'library-hash', [{
            'from': 'アンティーカ', 'to': "L'Antica", 'matching_title_album_duration_records': 61}])

    def test_map_rejects_other_library_and_stale_track_title(self):
        with self.assertRaises(ValueError):
            IdentityMap(self.document, self.tracks, 'another-library')
        stale = copy.deepcopy(self.document)
        stale['track_title_aliases'][0]['original_title'] = 'changed'
        with self.assertRaises(ValueError):
            IdentityMap(stale, self.tracks, 'library-hash')

    def test_pending_translation_aliases_are_inactive_and_preserve_versions(self):
        self.assertEqual(self.document['track_title_aliases'][0]['aliases'][0]['value'], '曲 (Off Vocal)')
        matcher = IdentityMap(self.document, self.tracks, 'library-hash')
        remote = {'title': '曲', 'artists': [{'name': 'アンティーカ'}], 'duration_seconds': 100}
        self.assertEqual(matcher.match(remote), [])
        enabled = copy.deepcopy(self.document)
        enabled['track_title_aliases'][1]['aliases'][0]['enabled'] = True
        rows = IdentityMap(enabled, self.tracks, 'library-hash').match(remote)
        self.assertEqual([row['persistent_id'] for row in rows], ['B'])
        self.assertEqual(rows[0]['title_match'], 'explicit_alias')
        self.assertEqual(rows[0]['identity_status'], 'unverified')

    def test_artist_alias_requires_duration_and_reports_album_discrepancy(self):
        matcher = IdentityMap(self.document, self.tracks, 'library-hash')
        remote = {'title': '曲 - Song', 'artists': [{'name': 'アンティーカ'}],
                  'duration_seconds': 101, 'album': {'name': 'Other release'}}
        row, = matcher.match(remote)
        self.assertEqual(row['persistent_id'], 'B')
        self.assertEqual(row['artist_match'], 'explicit_alias')
        self.assertEqual(row['duration_difference_seconds'], 1)
        self.assertFalse(row['album_match'])
        self.assertEqual(matcher.match({**remote, 'duration_seconds': None}), [])
        self.assertEqual(matcher.match({**remote, 'duration_seconds': 104}), [])
        self.assertEqual(matcher.match({**remote, 'artists': []}), [])

    def test_conflicting_alias_groups_are_rejected(self):
        document = copy.deepcopy(self.document)
        document['artist_alias_groups'].append({'enabled': True, 'aliases': ["L'Antica", 'Different Artist']})
        with self.assertRaises(ValueError):
            IdentityMap(document, self.tracks, 'library-hash')

    def test_preview_remains_separate_and_preserves_instrumental_version(self):
        matcher = IdentityMap(self.document, self.tracks, 'library-hash')
        remote = {'title': '曲', 'artists': [{'name': 'アンティーカ'}], 'duration_seconds': 100}
        self.assertEqual(matcher.match(remote), [])
        pending = matcher.match(remote, include_proposed=True)
        self.assertEqual([row['persistent_id'] for row in pending], ['B'])
        self.assertEqual(pending[0]['alias_review_status'], 'needs_review')
        instrumental = {**remote, 'title': '曲 (Off Vocal)'}
        self.assertEqual([row['persistent_id'] for row in matcher.match(instrumental, include_proposed=True)], ['A'])
        self.assertEqual(matcher.match(remote), [])

    def test_remote_translation_suffix_is_only_a_preview_until_reviewed(self):
        tracks = [{'persistent_id': 'C', 'title': '曲', 'artist': 'Artist', 'duration_ms': 100000}]
        document = build_map(tracks, 'hash')
        matcher = IdentityMap(document, tracks, 'hash')
        remote = {'title': '曲 - Song', 'artists': [{'name': 'Artist'}], 'duration_seconds': 100}
        self.assertEqual(matcher.match(remote), [])
        self.assertEqual(matcher.match(remote, include_proposed=True)[0]['title_match'], 'proposed_alias')
        decisions = {'schema_version': 1, 'library_sha256': 'hash', 'decisions': [
            {'persistent_id': 'C', 'original_title': '曲', 'alias': '曲 - Song', 'reason': 'Reviewed spelling'}]}
        reviewed = apply_title_review(document, decisions, tracks, 'hash')
        self.assertEqual(document['track_title_aliases'], [])
        matched = IdentityMap(reviewed, tracks, 'hash').match(remote)
        self.assertEqual(matched[0]['title_match'], 'explicit_alias')
        self.assertEqual(matched[0]['identity_status'], 'unverified')

    def test_review_rejects_remix_and_live_markers_even_with_underscore(self):
        for suffix, marker in (('_RMX', 'remix'), ('_REMIX', 'remix'), ('_LIVE', 'live'),
                               (' (Off Vocal)', 'instrumental'), (' (Game Size)', 'game_size'),
                               (' (2023 Ver.)', 'year:2023')):
            with self.subTest(suffix=suffix):
                title = 'REFRESH' + suffix
                self.assertIn(marker, version_signature(title))
                tracks = [{'persistent_id': 'R', 'title': title, 'artist': 'Artist', 'duration_ms': 100000}]
                document = build_map(tracks, 'hash')
                decisions = {'schema_version': 1, 'library_sha256': 'hash', 'decisions': [
                    {'persistent_id': 'R', 'original_title': title, 'alias': 'REFRESH', 'reason': 'Unsafe removal'}]}
                with self.assertRaises(ValueError):
                    apply_title_review(document, decisions, tracks, 'hash')

    def test_snapshot_validates_hash_ids_and_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tracks.json'
            track = {'persistent_id': '0000000000000001', 'title': '曲', 'artist': 'Artist', 'duration_ms': 100000}
            valid = {'source_sha256': 'a' * 64, 'tracks': [track]}
            path.write_text(json.dumps(valid))
            tracks, digest, provenance = load_library(track_snapshot=path)
            self.assertEqual(tracks, [track])
            self.assertEqual(digest, 'a' * 64)
            self.assertFalse(provenance['original_library_hash_verified_this_run'])
            for broken in ({**valid, 'source_sha256': 'bad'},
                           {**valid, 'tracks': [track, track]},
                           {**valid, 'tracks': [{**track, 'duration_ms': -1}]},
                           {**valid, 'tracks': [{**track, 'persistent_id': 'bad'}]}):
                with self.subTest(broken=broken):
                    path.write_text(json.dumps(broken))
                    with self.assertRaises(ValueError):
                        load_library(track_snapshot=path)

    def test_snapshot_cli_never_reads_original_library_or_activates_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracks = [{'persistent_id': '0000000000000001', 'title': '曲 - Song', 'artist': 'Artist', 'duration_ms': 100000}]
            snapshot = root / 'tracks.json'
            snapshot.write_text(json.dumps({'source_sha256': 'a' * 64, 'tracks': tracks}))
            aliases = root / 'map.json'
            aliases.write_text(json.dumps(build_map(tracks, 'a' * 64)))
            observations = root / 'observations.json'
            observations.write_text(json.dumps({'observations': [
                {'source': 'ytmusic', 'relation': 'radio', 'is_seed': False, 'position': 1,
                 'observed_at': 'time', 'track': {'video_id': 'example1234', 'title': '曲',
                                                'artists': [{'name': 'Artist'}], 'duration_seconds': 100}}]}))
            output = root / 'matched.json'
            argv = ['match', '--track-snapshot', str(snapshot), '--identity-map', str(aliases),
                    '--observations', str(observations), '--review-proposed', '--output', str(output)]
            with patch.object(sys, 'argv', argv), patch('sys.stdout', new=io.StringIO()), \
                    patch('music_identity_map.decode_musicdb', side_effect=AssertionError('No HDD access')):
                main()
            report = json.loads(output.read_text())
            self.assertEqual(report['library_input']['kind'], 'track_snapshot')
            self.assertEqual(report['summary']['nonseed_union_video_ids'], 0)
            self.assertEqual(report['summary']['new_pending_review_video_ids'], 1)
            self.assertEqual(report['summary']['verified_recordings'], 0)


    def test_review_cli_rejects_wrong_map_revision_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracks = [{'persistent_id': '0000000000000001', 'title': '曲', 'artist': 'Artist', 'duration_ms': 100000}]
            snapshot = root / 'tracks.json'
            snapshot.write_text(json.dumps({'source_sha256': 'a' * 64, 'tracks': tracks}))
            aliases = root / 'map.json'
            aliases.write_text(json.dumps(build_map(tracks, 'a' * 64)))
            decisions = root / 'review.json'
            decisions.write_text(json.dumps({'schema_version': 1, 'library_sha256': 'a' * 64,
                                             'identity_map_sha256': 'wrong-revision', 'decisions': []}))
            output = root / 'new-map.json'
            argv = ['review', '--track-snapshot', str(snapshot), '--identity-map', str(aliases),
                    '--review-decisions', str(decisions), '--output', str(output)]
            with patch.object(sys, 'argv', argv):
                with self.assertRaisesRegex(ValueError, 'different map revision'):
                    main()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
