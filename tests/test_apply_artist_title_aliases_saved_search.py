"""Strict saved-search source support for reviewed title spelling aliases."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from apply_artist_title_aliases import apply_reviewed_title_aliases
from music_identity_map import build_map


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class SavedSearchTitleAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / 'tracks.json'
        self.map_path = self.root / 'map.json'
        self.cache = self.root / 'search.json'
        self.audit = self.root / 'report.json'
        self.proposals = self.root / 'proposals.json'
        self.review = self.root / 'review.json'
        self.track = {'persistent_id': '0000000000000001', 'title': '日本語曲',
                      'artist': 'Local Artist', 'album': 'Official Album',
                      'duration_ms': 180000}
        self.alias = '日本語曲 - English Song'
        self.video_id = 'abcdefghijk'
        self.result = {'videoId': self.video_id, 'title': self.alias,
                       'artists': [{'name': 'Local Artist'}],
                       'album': {'name': 'Official Album'}, 'duration_seconds': 180}
        self.envelope = {'endpoint': 'search',
                         'arguments': {'query': '日本語曲 Local Artist',
                                       'filter': 'songs', 'limit': 5},
                         'observed_at': '2026-10-15T00:00:00+00:00',
                         'response': [self.result]}
        self.snapshot.write_text(json.dumps({'source_sha256': 'a' * 64,
                                             'tracks': [self.track]}, ensure_ascii=False))
        self.map_doc = build_map([self.track], 'a' * 64)
        self.map_path.write_text(json.dumps(self.map_doc, ensure_ascii=False))
        self.map_hash = _sha(self.map_path.read_bytes())
        self.example = {'source_kind': 'saved_search_result', 'result_position': 0,
                        'video_id': self.video_id, 'remote_title': self.alias,
                        'observation_file': str(self.cache)}
        self.candidate = {'persistent_id': self.track['persistent_id'],
                          'local_title': self.track['title'],
                          'remote_title_alias': self.alias,
                          'cached_observations': 1, 'unique_video_ids': 1,
                          'unique_video_pid_pairs': 1, 'max_duration_delta_ms': 0,
                          'exact_album_observations': 1, 'version_signature': [],
                          'examples': [self.example],
                          'source_observation_files': [str(self.cache)]}
        self.audit_doc = {'schema_version': 1,
                          'library': {'identity_map_sha256': self.map_hash},
                          'input_files': [], 'candidates': [self.candidate]}
        self.proposal = {'schema_version': 1, 'source_report': {},
                         'proposals': [{'persistent_id': self.track['persistent_id'],
                                        'local_title': self.track['title'],
                                        'alias': self.alias,
                                        'reason': 'Exact saved search candidate with complete credit, album, and duration.'}]}
        self.review_doc = {'schema_version': 1, 'library_sha256': 'a' * 64,
                           'identity_map_sha256': self.map_hash,
                           'proposal_sha256': '', 'source_report_sha256': '',
                           'approval': {'reviewed_by': 'root',
                                        'recording_identity_verified': False,
                                        'new_recording_edges_authorized': False},
                           'decisions': [{'persistent_id': self.track['persistent_id'],
                                          'original_title': self.track['title'],
                                          'alias': self.alias,
                                          'reason': self.proposal['proposals'][0]['reason']}]}
        self._write_bound_inputs()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_bound_inputs(self):
        self.cache.write_text(json.dumps(self.envelope, ensure_ascii=False))
        self.audit_doc['input_files'] = [{'path': str(self.cache),
                                          'sha256': _sha(self.cache.read_bytes())}]
        self.audit.write_text(json.dumps(self.audit_doc, ensure_ascii=False))
        source_sha = _sha(self.audit.read_bytes())
        self.proposal['source_report'] = {
            'path': str(self.audit), 'sha256': source_sha,
            'library_sha256': 'a' * 64,
            'track_snapshot_sha256': _sha(self.snapshot.read_bytes()),
            'identity_map_sha256': self.map_hash}
        self.proposals.write_text(json.dumps(self.proposal, ensure_ascii=False))
        self.review_doc['proposal_sha256'] = _sha(self.proposals.read_bytes())
        self.review_doc['source_report_sha256'] = source_sha
        self.review.write_text(json.dumps(self.review_doc, ensure_ascii=False))

    def _apply(self):
        return apply_reviewed_title_aliases(
            track_snapshot=self.snapshot, identity_map_path=self.map_path,
            proposals_path=self.proposals, review_decisions_path=self.review,
            output_path=self.root / 'out.json', receipt_path=self.root / 'receipt.json',
            _write_outputs=False)

    def test_accepts_exact_saved_search_result_through_existing_identity_gates(self):
        receipt = self._apply()
        self.assertEqual(receipt['activated_title_alias_count'], 1)
        self.assertEqual(receipt['verified_cached_example_count'], 1)
        self.assertFalse(receipt['recording_identity_verified'])
        self.assertEqual(receipt['recording_edges_added'], 0)

    def test_rejects_malformed_result_positions(self):
        for position in (True, -1, 0.0, '0', None):
            with self.subTest(position=position):
                self.example['result_position'] = position
                self._write_bound_inputs()
                with self.assertRaisesRegex(ValueError, 'invalid result_position'):
                    self._apply()

    def test_rejects_result_position_outside_response(self):
        self.example['result_position'] = 1
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'outside its response'):
            self._apply()

    def test_rejects_non_search_endpoint_and_invalid_search_arguments(self):
        self.envelope['endpoint'] = 'get_album'
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'not from a search response'):
            self._apply()
        self.envelope['endpoint'] = 'search'
        self.envelope['arguments']['filter'] = 'albums'
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'invalid search arguments'):
            self._apply()

    def test_rejects_cache_hash_change_before_reading_result(self):
        self.envelope['response'][0]['title'] = 'Changed title'
        self.cache.write_text(json.dumps(self.envelope, ensure_ascii=False))
        with self.assertRaisesRegex(ValueError, 'cached observation used by title review changed'):
            self._apply()

    def test_rejects_wrong_selected_video_and_title(self):
        self.envelope['response'][0]['videoId'] = 'differentvideo'
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'selected videoId differs'):
            self._apply()
        self.envelope['response'][0]['videoId'] = self.video_id
        self.envelope['response'][0]['title'] = '日本語曲 - Different English Title'
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'selected title differs'):
            self._apply()

    def test_rejects_wrong_album_artist_and_duration(self):
        self.envelope['response'][0]['album'] = {'name': 'Different Album'}
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'album, version, or duration gates'):
            self._apply()
        self.envelope['response'][0]['album'] = {'name': 'Official Album'}
        self.envelope['response'][0]['artists'] = [{'name': 'Different Artist'}]
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'Complete credit does not resolve uniquely'):
            self._apply()
        self.envelope['response'][0]['artists'] = [{'name': 'Local Artist'}]
        self.envelope['response'][0]['duration_seconds'] = 184
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'album, version, or duration gates'):
            self._apply()

    def test_rejects_version_changing_alias(self):
        version_alias = self.track['title'] + ' - English Song (Off Vocal)'
        self.alias = version_alias
        self.example['remote_title'] = version_alias
        self.envelope['response'][0]['title'] = version_alias
        self.candidate['remote_title_alias'] = version_alias
        self.proposal['proposals'][0]['alias'] = version_alias
        self.review_doc['decisions'][0]['alias'] = version_alias
        self._write_bound_inputs()
        with self.assertRaisesRegex(ValueError, 'version facets'):
            self._apply()


if __name__ == '__main__':
    unittest.main()
