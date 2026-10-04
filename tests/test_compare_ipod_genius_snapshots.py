"""Paired report comparison stays aggregate-only and rejects weak evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import compare_ipod_genius_snapshots as compare


def report(files, **extra):
    value = dict(report_version=1, max_file_bytes=32 * 1024 * 1024, files=files,
                 read_only=True, ipod_acceptance_verified=False)
    value.update(extra)
    return value


def ipod_db(*, digest='a' * 64, tracks=2, cuid_present=True, database_version=19):
    return dict(bytes=1024, sha256=digest, format='itunes_db', status='structure_valid', header_bytes=244,
                database_version=database_version,
                sections=[dict(type=1, header_bytes=96, total_bytes=900,
                               nested_records=dict(status='validated')),
                          dict(type=2, header_bytes=96, total_bytes=400,
                               nested_records=dict(status='validated')),
                          dict(type=9, header_bytes=16, total_bytes=48,
                               cuid_present=cuid_present, cuid_bytes=32 if cuid_present else 0,
                               expected_cuid_length=cuid_present)],
                nested_records_validated=True,
                nested_coverage='libgpod_track_and_playlist_records',
                track_records=tracks, track_ids_unique=tracks, track_ids_duplicate_records=0,
                dbid_nonzero_records=tracks, dbid_unique_values=tracks, dbid_duplicate_records=0,
                dbid2_available_records=tracks, dbid2_nonzero_records=tracks,
                dbid2_unique_values=tracks, dbid2_duplicate_records=0,
                dbid_equal_dbid2_nonzero_records=0, playlists=3, playlist_members=tracks * 2,
                playlist_unique_track_ids_referenced=tracks, dangling_playlist_members=0)


def sqlite_db(*, digest='b' * 64, metadata=2, schema_version=7):
    return dict(bytes=4096, sha256=digest, format='sqlite', status='sqlite_readable',
                user_version=1, schema_version=schema_version, table_count=3, unknown_table_count=0,
                tables=dict(genius_metadata=metadata, genius_similarities=metadata, genius_config=1),
                relations=dict(envelope_profile='observed_music_v1_uncompressed',
                               examined_edges=metadata * 3, dangling_edges=0,
                               seeds_missing_metadata=0, unsupported_rows=0, complete=True,
                               ipod_profile_verified=False))


class SnapshotComparisonTests(unittest.TestCase):
    def test_compares_aggregate_invariants_without_claiming_success(self):
        before = report({'iTunesDB': ipod_db(), 'Genius.itdb': sqlite_db()})
        after = report({'iTunesDB': ipod_db(digest='c' * 64, tracks=3),
                        'Genius.itdb': sqlite_db(digest='d' * 64, metadata=4)})
        result = compare.compare_reports(before, after)
        self.assertEqual(result['same_db_profile'], 'same')
        self.assertFalse(result['comparison_partial'])
        self.assertTrue(result['files']['iTunesDB']['content_hash_changed'])
        self.assertEqual(result['files']['iTunesDB']['profile']['status'], 'unchanged')
        self.assertEqual(result['files']['iTunesDB']['itunesdb_aggregates']['track_records']['delta'], 1)
        self.assertEqual(result['files']['iTunesDB']['cuid']['cuid_present_sections']['status'], 'unchanged')
        self.assertEqual(result['files']['Genius.itdb']['sqlite_table_counts']['genius_metadata']['delta'], 2)
        self.assertEqual(result['files']['Genius.itdb']['sqlite_relation_envelope']['interpretation'],
                         'observed_music_v1_only')
        self.assertFalse(result['genius_generation_proven'])
        self.assertFalse(result['device_acceptance_proven'])

    def test_profile_drift_and_new_or_missing_known_files_are_explicit(self):
        encrypted = dict(bytes=4096, sha256='1' * 64, format='possible_music_encrypted_sqlite',
                         status='unsupported_encryption')
        before = report({'iTunesDB': ipod_db(), 'Genius.itdb': encrypted})
        after = report({'iTunesDB': ipod_db(digest='2' * 64, database_version=20),
                        'iTunes Library.itlp/Genius.itdb': encrypted})
        result = compare.compare_reports(before, after)
        self.assertEqual(result['same_db_profile'], 'drift')
        self.assertIn('iTunes Library.itlp/Genius.itdb', result['files_added_after'])
        self.assertIn('Genius.itdb', result['files_missing_after'])
        self.assertEqual(result['files']['Genius.itdb']['profile']['status'], 'not_comparable')
        self.assertEqual(result['files']['Genius.itdb']['itunesdb_aggregates']['status'], 'not_applicable')
        self.assertTrue(result['comparison_partial'])
        self.assertFalse(result['device_acceptance_proven'])

    def test_unsupported_file_disappearance_is_not_mislabeled_profile_drift(self):
        encrypted = dict(bytes=4096, sha256='8' * 64, format='possible_music_encrypted_sqlite',
                         status='unsupported_encryption')
        before = report({'iTunesDB': ipod_db(), 'Genius.itdb': encrypted})
        after = report({'iTunesDB': ipod_db(digest='9' * 64)})
        result = compare.compare_reports(before, after)
        self.assertEqual(result['same_db_profile'], 'not_comparable')
        self.assertEqual(result['files_missing_after'], ['Genius.itdb'])
        self.assertTrue(result['comparison_partial'])

    def test_no_change_pair_reports_unchanged_observations(self):
        snapshot = report({'iTunesDB': ipod_db(), 'Genius.itdb': sqlite_db()})
        result = compare.compare_reports(snapshot, json.loads(json.dumps(snapshot)))
        self.assertEqual(result['same_db_profile'], 'same')
        self.assertFalse(result['comparison_partial'])
        self.assertFalse(result['files']['iTunesDB']['content_hash_changed'])
        self.assertEqual(result['files']['iTunesDB']['itunesdb_aggregates']['track_records']['status'],
                         'unchanged')
        self.assertEqual(result['files']['Genius.itdb']['sqlite_table_counts']['genius_metadata']['status'],
                         'unchanged')

    def test_empty_reports_are_partial_and_not_comparable(self):
        result = compare.compare_reports(report({}), report({}))
        self.assertEqual(result['same_db_profile'], 'not_comparable')
        self.assertTrue(result['comparison_partial'])
        self.assertEqual(result['files'], {})

    def test_unsupported_or_missing_nested_metrics_are_not_compared(self):
        unsupported = ipod_db()
        unsupported['nested_records_validated'] = False
        encrypted = dict(bytes=4096, sha256='3' * 64, format='possible_music_encrypted_sqlite',
                         status='unsupported_encryption')
        result = compare.compare_reports(report({'iTunesDB': unsupported, 'Genius.itdb': encrypted}),
                                        report({'iTunesDB': ipod_db(digest='4' * 64),
                                                'Genius.itdb': encrypted}))
        self.assertEqual(result['files']['iTunesDB']['itunesdb_aggregates']['status'], 'not_comparable')
        self.assertEqual(result['files']['Genius.itdb']['profile']['status'], 'not_comparable')
        self.assertEqual(result['same_db_profile'], 'not_comparable')
        self.assertTrue(result['comparison_partial'])
        self.assertFalse(result['genius_generation_proven'])

    def test_unexpected_identifiers_and_paths_are_suppressed(self):
        secret = 'PERSONAL_CUID_9f3b71d8'
        before = report({'iTunesDB': ipod_db(), '/private/' + secret: {'token': secret}},
                        account_id=secret)
        after = report({'iTunesDB': ipod_db(digest='5' * 64)},
                       user_token=secret)
        before['files']['iTunesDB']['raw_genius_id'] = secret
        before['files']['iTunesDB']['tables'] = {'private_' + secret: 99}
        result = compare.compare_reports(before, after)
        encoded = json.dumps(result)
        self.assertEqual(result['unknown_file_entries']['before'], 1)
        self.assertNotIn(secret, encoded)
        self.assertNotIn('/private/', encoded)
        self.assertNotIn('raw_genius_id', encoded)
        self.assertNotIn('account_id', encoded)

    def test_malformed_nested_report_fields_are_not_echoed_or_trusted(self):
        secret = 'SEED_ID_123456789'
        snapshot = ipod_db()
        snapshot['sections'][0]['nested_records'] = secret
        result = compare.compare_reports(report({'iTunesDB': snapshot}),
                                        report({'iTunesDB': ipod_db(digest='7' * 64)}))
        encoded = json.dumps(result)
        self.assertEqual(result['files']['iTunesDB']['itunesdb_aggregates']['status'], 'not_comparable')
        self.assertTrue(result['comparison_partial'])
        self.assertNotIn(secret, encoded)

    def test_invalid_reports_and_oversized_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid_report'):
            compare.normalize_report(report({}, ipod_acceptance_verified=True))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'snapshot.json'
            path.write_text('{}', encoding='utf-8')
            with patch.object(compare, 'MAX_REPORT_BYTES', 1):
                with self.assertRaisesRegex(ValueError, 'report_size_limit'):
                    compare.read_report(path)

    def test_cli_writes_only_a_new_file(self):
        script = Path(compare.__file__)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = root / 'before.json'
            after = root / 'after.json'
            output = root / 'delta.json'
            before.write_text(json.dumps(report({'iTunesDB': ipod_db()})), encoding='utf-8')
            after.write_text(json.dumps(report({'iTunesDB': ipod_db(digest='6' * 64)})), encoding='utf-8')
            first = subprocess.run([sys.executable, str(script), str(before), str(after),
                                    '--output', str(output)], capture_output=True)
            self.assertEqual(first.returncode, 0)
            original = output.read_bytes()
            second = subprocess.run([sys.executable, str(script), str(before), str(after),
                                     '--output', str(output)], capture_output=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(output.read_bytes(), original)
            self.assertNotIn(b'aaaa', original)


if __name__ == '__main__':
    unittest.main()
