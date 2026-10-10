"""Cache-bound root-reviewed title aliases preserve metadata and identity scope."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from apply_artist_title_aliases import apply_reviewed_title_aliases
from music_identity_map import build_map, load_library, normalize, IdentityMap


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class ApplyArtistTitleAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / 'tracks.json'
        self.map_path = self.root / 'map.json'
        self.cache = self.root / 'cache.json'
        self.audit = self.root / 'audit.json'
        self.proposals = self.root / 'proposals.json'
        self.review = self.root / 'review.json'
        self.out = self.root / 'map-title.json'
        self.receipt = self.root / 'receipt.json'
        self.library_hash = 'a' * 64
        self.tracks = [{'persistent_id': '0000000000000001', 'title': '日本語曲',
                        'artist': 'Local Character (CV: Local Actor)', 'album': 'Official Album',
                        'duration_ms': 180000}]
        self.snapshot.write_text(json.dumps({'source_sha256': self.library_hash,
                                             'tracks': self.tracks}, ensure_ascii=False))
        self.map = build_map(self.tracks, self.library_hash)
        self.map['artist_credit_aliases'] = [{
            'remote_credit': 'REMOTE CHARACTER (CV: Remote Actor)',
            'local_credit': self.tracks[0]['artist'], 'enabled': True,
            'status': 'reviewed_whole_credit_alias', 'evidence': {'source_report_sha256': 'b' * 64}}]
        self.map_path.write_text(json.dumps(self.map, ensure_ascii=False))
        self.map_hash = _sha(self.map_path.read_bytes())
        self.remote = {'video_id': 'abcdefghijk', 'title': '日本語曲 - English Song',
                       'artists': [{'name': 'REMOTE CHARACTER (CV: Remote Actor)'}],
                       'album': {'name': 'Official Album'}, 'length': '3:00'}
        self.cache.write_text(json.dumps({'observations': [{'track': self.remote}]}, ensure_ascii=False))
        self.candidate = {'persistent_id': self.tracks[0]['persistent_id'],
                          'local_title': self.tracks[0]['title'],
                          'remote_title_alias': self.remote['title'],
                          'cached_observations': 1, 'unique_video_ids': 1,
                          'unique_video_pid_pairs': 1, 'max_duration_delta_ms': 0,
                          'exact_album_observations': 1,
                          'version_signature': [],
                          'examples': [{'video_id': self.remote['video_id'],
                                        'remote_title': self.remote['title'],
                                        'observation_file': str(self.cache)}],
                          'source_observation_files': [str(self.cache)]}
        self.audit_doc = {'schema_version': 1,
                          'library': {'identity_map_sha256': self.map_hash},
                          'input_files': [{'path': str(self.cache),
                                           'sha256': _sha(self.cache.read_bytes())}],
                          'candidates': [self.candidate]}
        self.audit.write_text(json.dumps(self.audit_doc, ensure_ascii=False))
        self.report_hash = _sha(self.audit.read_bytes())
        self.proposal = {'schema_version': 1, 'source_report': {
            'path': str(self.audit), 'sha256': self.report_hash,
            'library_sha256': self.library_hash,
            'track_snapshot_sha256': _sha(self.snapshot.read_bytes()),
            'identity_map_sha256': self.map_hash},
            'proposals': [{'persistent_id': self.tracks[0]['persistent_id'],
                           'local_title': self.tracks[0]['title'],
                           'alias': self.remote['title'],
                           'reason': 'Exact Japanese prefix, one full-credit PID, exact album and duration.'}]}
        self.proposals.write_text(json.dumps(self.proposal, ensure_ascii=False))
        self.proposal_hash = _sha(self.proposals.read_bytes())
        self.review_doc = {'schema_version': 1, 'library_sha256': self.library_hash,
                           'identity_map_sha256': self.map_hash,
                           'proposal_sha256': self.proposal_hash,
                           'source_report_sha256': self.report_hash,
                           'approval': {'reviewed_by': 'root',
                                        'recording_identity_verified': False,
                                        'new_recording_edges_authorized': False},
                           'decisions': [{'persistent_id': self.tracks[0]['persistent_id'],
                                          'original_title': self.tracks[0]['title'],
                                          'alias': self.remote['title'],
                                          'reason': self.proposal['proposals'][0]['reason']}]}
        self.review.write_text(json.dumps(self.review_doc, ensure_ascii=False))

    def tearDown(self):
        self.tmp.cleanup()

    def _refresh_snapshot_bindings(self):
        self.proposal['source_report']['track_snapshot_sha256'] = _sha(self.snapshot.read_bytes())
        self.proposals.write_text(json.dumps(self.proposal, ensure_ascii=False))
        self.proposal_hash = _sha(self.proposals.read_bytes())
        self.review_doc['proposal_sha256'] = self.proposal_hash
        self.review.write_text(json.dumps(self.review_doc, ensure_ascii=False))

    def _refresh_cache_bindings(self):
        self.cache.write_text(json.dumps({'observations': [{'track': self.remote}]}, ensure_ascii=False))
        self.audit_doc['input_files'][0]['sha256'] = _sha(self.cache.read_bytes())
        self.audit.write_text(json.dumps(self.audit_doc, ensure_ascii=False))
        self.report_hash = _sha(self.audit.read_bytes())
        self.proposal['source_report']['sha256'] = self.report_hash
        self.proposals.write_text(json.dumps(self.proposal, ensure_ascii=False))
        self.proposal_hash = _sha(self.proposals.read_bytes())
        self.review_doc['proposal_sha256'] = self.proposal_hash
        self.review_doc['source_report_sha256'] = self.report_hash
        self.review.write_text(json.dumps(self.review_doc, ensure_ascii=False))

    def test_applies_only_exact_noncolliding_title_alias(self):
        result = apply_reviewed_title_aliases(
            track_snapshot=self.snapshot, identity_map_path=self.map_path,
            proposals_path=self.proposals, review_decisions_path=self.review,
            output_path=self.out, receipt_path=self.receipt)
        tracks, library_hash, _ = load_library(track_snapshot=self.snapshot)
        matcher = IdentityMap(json.loads(self.out.read_text()), tracks, library_hash)
        matches = matcher.match(self.remote)
        self.assertEqual([row['persistent_id'] for row in matches], ['0000000000000001'])
        self.assertEqual(matches[0]['title_match'], 'explicit_alias')
        self.assertEqual(matches[0]['artist_match'], 'explicit_artist_credit_alias')
        self.assertEqual(result['activated_title_alias_count'], 1)
        self.assertFalse(result['recording_identity_verified'])
        self.assertEqual(result['recording_edges_added'], 0)
        self.assertEqual(matcher.tracks, self.tracks)

    def test_accepts_complete_exact_group_credit_without_credit_alias(self):
        # A group artist can already match the complete local credit exactly; a
        # title alias should not require an unnecessary artist-credit alias.
        self.remote['artists'] = [{'name': self.tracks[0]['artist']}]
        self._refresh_cache_bindings()
        result = apply_reviewed_title_aliases(
            track_snapshot=self.snapshot, identity_map_path=self.map_path,
            proposals_path=self.proposals, review_decisions_path=self.review,
            output_path=self.out, receipt_path=self.receipt)
        self.assertEqual(result['activated_title_alias_count'], 1)
        self.assertEqual(result['verified_cached_example_count'], 1)
        tracks, library_hash, _ = load_library(track_snapshot=self.snapshot)
        matcher = IdentityMap(json.loads(self.out.read_text()), tracks, library_hash)
        match = matcher.match(self.remote)
        self.assertEqual([row['persistent_id'] for row in match], ['0000000000000001'])
        self.assertEqual(match[0]['artist_match'], 'exact')
        self.assertEqual(match[0]['artist_credit_match'], 'complete')
        self.assertFalse(result['recording_identity_verified'])
        self.assertEqual(result['recording_edges_added'], 0)

    def test_rejects_ambiguous_forced_pid_even_when_one_title_proposal_exists(self):
        # Duplicate title/credit/duration rows make the forced Japanese-title
        # rematch ambiguous; the title alias cannot choose one PID for us.
        other = dict(self.tracks[0], persistent_id='0000000000000002')
        self.tracks.append(other)
        self.snapshot.write_text(json.dumps({'source_sha256': self.library_hash,
                                             'tracks': self.tracks}, ensure_ascii=False))
        self._refresh_snapshot_bindings()
        with self.assertRaisesRegex(ValueError, 'Complete credit does not resolve uniquely'):
            apply_reviewed_title_aliases(
                track_snapshot=self.snapshot, identity_map_path=self.map_path,
                proposals_path=self.proposals, review_decisions_path=self.review,
                output_path=self.out, receipt_path=self.receipt)

    def test_rejects_changed_cached_source_before_activation(self):
        self.remote['album'] = {'name': 'Changed Album'}
        self.cache.write_text(json.dumps({'observations': [{'track': self.remote}]}, ensure_ascii=False))
        with self.assertRaisesRegex(ValueError, 'cached observation used by title review changed'):
            apply_reviewed_title_aliases(
                track_snapshot=self.snapshot, identity_map_path=self.map_path,
                proposals_path=self.proposals, review_decisions_path=self.review,
                output_path=self.out, receipt_path=self.receipt)

    def test_rejects_alias_collision_with_another_active_pid(self):
        other = {'persistent_id': '0000000000000002', 'title': self.remote['title'],
                 'artist': 'Other', 'album': 'Other Album', 'duration_ms': 180000}
        self.tracks.append(other)
        self.snapshot.write_text(json.dumps({'source_sha256': self.library_hash,
                                             'tracks': self.tracks}, ensure_ascii=False))
        self._refresh_snapshot_bindings()
        with self.assertRaisesRegex(ValueError, 'collides with an active'):
            apply_reviewed_title_aliases(
                track_snapshot=self.snapshot, identity_map_path=self.map_path,
                proposals_path=self.proposals, review_decisions_path=self.review,
                output_path=self.out, receipt_path=self.receipt)


if __name__ == '__main__':
    unittest.main()
