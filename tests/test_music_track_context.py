import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_track_context import build_context_report, build_sidecar, main, validate_sidecar


class MusicTrackContextTests(unittest.TestCase):
    def setUp(self):
        self.library_hash = 'a' * 64
        self.tracks = [
            {'persistent_id': '0000000000000001', 'title': 'Theme (Game Size)',
             'artist': 'Artist', 'album': 'Game OST', 'duration_ms': 100000},
            {'persistent_id': '0000000000000002', 'title': 'Standalone Song',
             'artist': 'Artist', 'album': 'Album', 'duration_ms': 120000},
        ]
        self.sidecar = build_sidecar(self.tracks, self.library_hash)

    def claim(self, **overrides):
        claim = {
            'work_title': 'Example Game',
            'franchise': 'Example Series',
            'medium': 'game',
            'role': 'opening',
            'kind': 'vocal',
            'notes': 'Opening theme listed in the source credits.',
            'status': 'confirmed',
            'source': {'type': 'web', 'url': 'https://example.test/music',
                       'title': 'Official music page', 'evidence': 'Lists the song as the game opening.'},
        }
        claim.update(overrides)
        return claim

    def test_report_keeps_all_tracks_facets_and_multiple_evidenced_claims(self):
        self.sidecar['tracks'][0]['claims'] = [
            self.claim(),
            self.claim(work_title='Example Anime', medium='anime', role='ending',
                       status='provisional', source={'type': 'user', 'reference': 'personal note'}),
        ]
        report = build_context_report(self.tracks, self.library_hash, self.sidecar)
        self.assertEqual(len(report['tracks']), 2)
        self.assertEqual(len(report['tracks'][0]['claims']), 2)
        self.assertEqual(report['tracks'][1]['claims'], [])
        self.assertIn('game_size', report['tracks'][0]['version_facets']['markers'])
        self.assertEqual(report['tracks'][0]['metadata']['album'], 'Game OST')
        self.assertEqual(report['summary']['tracks_with_claims'], 1)
        self.assertEqual(report['summary']['tracks_with_work_title'], 1)
        self.assertEqual(report['summary']['claim_count'], 2)
        self.assertEqual(report['summary']['confirmed_claim_count'], 1)
        self.assertEqual(report['summary']['provisional_claim_count'], 1)
        self.assertEqual(report['identity_status'], 'unverified')
        self.assertEqual(report['recording_identity_claims'], 0)

    def test_drama_theme_has_its_own_medium_and_role(self):
        self.sidecar['tracks'][0]['claims'] = [self.claim(work_title='Example drama', medium='drama', role='theme')]
        report = build_context_report(self.tracks, self.library_hash, self.sidecar)
        self.assertEqual(report['summary']['medium_claim_counts'], {'drama': 1})
        self.assertEqual(report['summary']['role_claim_counts'], {'theme': 1})

    def test_unknown_context_values_are_retained(self):
        self.sidecar['tracks'][0]['claims'] = [self.claim(
            work_title=None, franchise=None, medium='unknown', role='unknown', kind='unknown',
            status='provisional', source={'type': 'local_metadata', 'fields': ['album', 'title']})]
        report = build_context_report(self.tracks, self.library_hash, self.sidecar)
        claim, = report['tracks'][0]['claims']
        self.assertIsNone(claim['work_title'])
        self.assertIsNone(claim['franchise'])
        self.assertEqual(claim['medium'], 'unknown')
        self.assertEqual(claim['role'], 'unknown')
        self.assertEqual(claim['kind'], 'unknown')

    def test_report_does_not_mutate_the_sidecar(self):
        self.sidecar['tracks'][0]['claims'] = [self.claim()]
        original = copy.deepcopy(self.sidecar)
        report = build_context_report(self.tracks, self.library_hash, self.sidecar)
        report['tracks'][0]['claims'][0]['notes'] = 'changed in report'
        self.assertEqual(self.sidecar, original)

    def test_sidecar_rejects_wrong_hash_unknown_or_duplicate_pid_and_bad_claim(self):
        bad_documents = []
        wrong_hash = copy.deepcopy(self.sidecar)
        wrong_hash['library_sha256'] = 'b' * 64
        bad_documents.append(wrong_hash)
        unknown_pid = copy.deepcopy(self.sidecar)
        unknown_pid['tracks'][0]['persistent_id'] = '0000000000000003'
        bad_documents.append(unknown_pid)
        duplicate_pid = copy.deepcopy(self.sidecar)
        duplicate_pid['tracks'][1]['persistent_id'] = duplicate_pid['tracks'][0]['persistent_id']
        bad_documents.append(duplicate_pid)
        invalid_role = copy.deepcopy(self.sidecar)
        invalid_role['tracks'][0]['claims'] = [self.claim(role='opening_credits')]
        bad_documents.append(invalid_role)
        invalid_kind = copy.deepcopy(self.sidecar)
        invalid_kind['tracks'][0]['claims'] = [self.claim(kind='same_recording')]
        bad_documents.append(invalid_kind)
        for document in bad_documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                validate_sidecar(document, self.tracks, self.library_hash)

    def test_source_validation_checks_local_fields_user_and_web_evidence(self):
        invalid_claims = [
            self.claim(source={'type': 'local_metadata', 'fields': ['composer']}),
            self.claim(source={'type': 'user', 'reference': ''}),
            self.claim(source={'type': 'web', 'url': 'javascript:alert(1)', 'title': 'Bad URL'}),
            self.claim(source={'type': 'web', 'url': 'https://user:pass@example.test/page', 'title': 'Credentials'}),
            self.claim(source={'type': 'web', 'url': 'https://example.test:99999/page', 'title': 'Bad port'}),
            self.claim(source={'type': 'web', 'url': 'https://example.test/page?token=secret', 'title': 'Token URL'}),
            self.claim(source={'type': 'web', 'url': 'https://example.test/page', 'title': 'Page', 'evidence': 'x' * 501}),
        ]
        for claim in invalid_claims:
            document = copy.deepcopy(self.sidecar)
            document['tracks'][0]['claims'] = [claim]
            with self.subTest(source=claim['source']), self.assertRaises(ValueError):
                validate_sidecar(document, self.tracks, self.library_hash)

    def test_cli_creates_hashed_report_from_saved_snapshot_and_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / 'tracks.json'
            snapshot_document = {'source_sha256': self.library_hash, 'tracks': self.tracks}
            snapshot.write_text(json.dumps(snapshot_document), encoding='utf-8')
            sidecar = root / 'context.json'
            self.sidecar['tracks'][0]['claims'] = [self.claim()]
            sidecar.write_text(json.dumps(self.sidecar), encoding='utf-8')
            output = root / 'report.json'
            argv = ['music_track_context', '--track-snapshot', str(snapshot), '--claims', str(sidecar),
                    '--output', str(output)]
            with patch.object(sys, 'argv', argv):
                main()
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['summary']['track_count'], 2)
            self.assertEqual(report['summary']['claim_count'], 1)
            self.assertEqual(report['network_requests'], 0)
            self.assertEqual(report['input_hashes']['track_snapshot_sha256'],
                             hashlib.sha256(snapshot.read_bytes()).hexdigest())
            self.assertEqual(report['sidecar_sha256'], hashlib.sha256(sidecar.read_bytes()).hexdigest())
            with patch.object(sys, 'argv', argv), self.assertRaises(SystemExit):
                main()  # Existing output is never overwritten.


if __name__ == '__main__':
    unittest.main()
