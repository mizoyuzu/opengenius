"""iPod audit validates boundaries and never exposes stored private values."""
import json
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import inspect_ipod_genius as audit


def database(payload, kind=9):
    section = b'mhsd' + struct.pack('<III', 16, 16 + len(payload), kind) + payload
    return b'mhbd' + struct.pack('<III', 20, 20 + len(section), 1) + struct.pack('<I', 0x30) + section


def mhod(payload=b''):
    return b'mhod' + struct.pack('<III', 16, 16 + len(payload), 1) + payload


def mhit(track_id, dbid, dbid2=None, children=(), header_len=0xf4):
    header = bytearray(header_len)
    header[:4] = b'mhit'
    struct.pack_into('<III', header, 4, header_len, header_len + sum(map(len, children)), len(children))
    struct.pack_into('<I', header, 16, track_id)
    struct.pack_into('<Q', header, 112, dbid)
    if dbid2 is not None and header_len >= 0xf4:
        struct.pack_into('<Q', header, 168, dbid2)
    return bytes(header) + b''.join(children)


def track_section(tracks, header_len=16):
    body = b'mhlt' + struct.pack('<II', 12, len(tracks)) + b''.join(tracks)
    return b'mhsd' + struct.pack('<III', header_len, header_len + len(body), 1) + bytes(header_len - 16) + body


def playlist_section(playlists, header_len=16):
    body = b'mhlp' + struct.pack('<II', 12, len(playlists)) + b''.join(playlists)
    return b'mhsd' + struct.pack('<III', header_len, header_len + len(body), 2) + bytes(header_len - 16) + body


def playlist(members, child_mhods=()):
    entries = b''.join(members)
    children = b''.join(child_mhods)
    header = bytearray(48)
    header[:4] = b'mhyp'
    struct.pack_into('<III', header, 4, 48, 48 + len(children) + len(entries), len(child_mhods))
    struct.pack_into('<I', header, 16, len(members))
    return bytes(header) + children + entries


def mhip(track_id, children=(), legacy_total=False):
    header = bytearray(36)
    header[:4] = b'mhip'
    struct.pack_into('<III', header, 4, 36, 36 if legacy_total else 36 + sum(map(len, children)), len(children))
    struct.pack_into('<I', header, 24, track_id)
    return bytes(header) + b''.join(children)


def database_sections(*sections):
    return b'mhbd' + struct.pack('<III', 20, 20 + sum(map(len, sections)), len(sections)) + struct.pack('<I', 0x30) + b''.join(sections)


class IPodAuditTests(unittest.TestCase):
    def test_cuid_is_redacted_and_length_checked(self):
        secret = b'private-cuid-value-12345678901234'
        report = audit.inspect_itunesdb(database(secret))
        self.assertEqual(report['status'], 'structure_valid')
        self.assertEqual(report['sections'][0]['cuid_bytes'], len(secret))
        self.assertNotIn(secret.decode(), json.dumps(report))
        self.assertFalse(report['checksum_validated'])
        self.assertTrue(audit.inspect_itunesdb(database(b'x' * 32))['sections'][0]['expected_cuid_length'])
        self.assertFalse(audit.inspect_itunesdb(database(b'x'))['sections'][0]['expected_cuid_length'])

    def test_truncated_and_invalid_chunk_bounds(self):
        good = database(b'x' * 32)
        for data in (good[:12], good[:-1], good + b'x'):
            self.assertTrue(audit.inspect_itunesdb(data)['status'].startswith('invalid'))
        for offset, value in ((4, 0), (8, 0), (24, 0), (28, 0), (28, 10000)):
            bad = bytearray(good)
            struct.pack_into('<I', bad, offset, value)
            self.assertTrue(audit.inspect_itunesdb(bad)['status'].startswith('invalid'))

    def test_ipod_track_ids_dbids_and_playlist_graph_are_aggregated(self):
        private_dbid = 0x7f234a9911023301
        private_dbid2 = 0x6e124a9988076602
        data = database_sections(
            track_section([mhit(17, private_dbid, private_dbid),
                           mhit(18, private_dbid, private_dbid2, [mhod(b'private title')])]),
            playlist_section([playlist([mhip(17), mhip(99, [mhod()], legacy_total=True)])]))
        report = audit.inspect_itunesdb(data)
        encoded = json.dumps(report)
        self.assertEqual(report['status'], 'structure_valid')
        self.assertTrue(report['nested_records_validated'])
        self.assertEqual(report['track_records'], 2)
        self.assertEqual(report['track_ids_unique'], 2)
        self.assertEqual(report['dbid_nonzero_records'], 2)
        self.assertEqual(report['dbid_unique_values'], 1)
        self.assertEqual(report['dbid_duplicate_records'], 1)
        self.assertEqual(report['dbid2_available_records'], 2)
        self.assertEqual(report['dbid2_unique_values'], 2)
        self.assertEqual(report['dbid_equal_dbid2_nonzero_records'], 1)
        self.assertEqual(report['playlists'], 1)
        self.assertEqual(report['playlist_members'], 2)
        self.assertEqual(report['playlist_unique_track_ids_referenced'], 2)
        self.assertEqual(report['dangling_playlist_members'], 1)
        self.assertNotIn(str(private_dbid), encoded)
        self.assertNotIn(str(private_dbid2), encoded)
        self.assertNotIn('private title', encoded)

    def test_nested_track_and_playlist_bounds_reject_malformed_records(self):
        bad_track = bytearray(mhit(17, 1, 2))
        struct.pack_into('<I', bad_track, 4, 0x98)
        track_report = audit.inspect_itunesdb(database_sections(track_section([bytes(bad_track)])))
        self.assertEqual(track_report['status'], 'invalid_nested_records')
        self.assertEqual(track_report['error'], 'invalid_mhit_bounds')

        bad_member = bytearray(mhip(17))
        struct.pack_into('<I', bad_member, 8, 0xffffffff)
        playlist_report = audit.inspect_itunesdb(database_sections(
            track_section([mhit(17, 1, 2)]), playlist_section([playlist([bytes(bad_member)])])))
        self.assertEqual(playlist_report['status'], 'invalid_nested_records')
        self.assertEqual(playlist_report['error'], 'invalid_mhip_bounds')

    def test_partial_results_are_redacted_on_later_error_and_trailing_bytes_are_reported(self):
        private_track_id = 0xabcdef01
        valid_track = track_section([mhit(private_track_id, 0x7f234a9911023301, 0x6e124a9988076602)])
        good_playlist = playlist_section([playlist([mhip(private_track_id)])])
        bad_playlist = bytearray(playlist_section([playlist([mhip(0xabcdef02)])]))
        member_offset = bad_playlist.find(b'mhip')
        struct.pack_into('<I', bad_playlist, member_offset + 8, 0xffffffff)
        invalid = audit.inspect_itunesdb(database_sections(valid_track, good_playlist, bytes(bad_playlist)))
        self.assertEqual(invalid['status'], 'invalid_nested_records')
        self.assertNotIn(str(private_track_id), json.dumps(invalid))
        self.assertNotIn(str(0x7f234a9911023301), json.dumps(invalid))

        trailing = bytearray(track_section([mhit(21, 22, 23)]))
        struct.pack_into('<I', trailing, 8, len(trailing) + 1)
        trailing.append(0xaa)
        report = audit.inspect_itunesdb(database_sections(bytes(trailing)))
        self.assertEqual(report['status'], 'structure_valid')
        self.assertFalse(report['nested_records_validated'])
        self.assertEqual(report['sections'][0]['nested_records']['status'],
                         'validated_with_unparsed_trailing_bytes')
        self.assertEqual(report['sections'][0]['nested_records']['unparsed_trailing_bytes'], 1)

    def test_unknown_nested_payload_is_not_claimed_as_parsed(self):
        report = audit.inspect_itunesdb(database(b'unknown track section', kind=1))
        self.assertEqual(report['status'], 'structure_valid')
        self.assertFalse(report['nested_records_validated'])
        self.assertEqual(report['sections'][0]['nested_records']['status'], 'unsupported_nested_layout')

    def test_plain_sqlite_graph_and_private_tables(self):
        connection = sqlite3.connect(':memory:')
        connection.executescript('CREATE TABLE genius_metadata(genius_id INTEGER);'
                                'CREATE TABLE genius_similarities(genius_id INTEGER, version INTEGER, data BLOB);'
                                'CREATE TABLE private_secret_account(value TEXT); PRAGMA user_version=7;')
        connection.executemany('INSERT INTO genius_metadata VALUES(?)', [(1,), (2,)])
        blob = struct.pack('<IIIQQ', 0, 20, 2, 2, 999)
        connection.execute('INSERT INTO genius_similarities VALUES(1,1,?)', (blob,))
        connection.execute('INSERT INTO private_secret_account VALUES(?)', ('private-key-cuid',))
        connection.commit()
        report = audit.inspect_sqlite(connection.serialize())
        connection.close()
        self.assertEqual(report['user_version'], 7)
        self.assertEqual(report['relations']['dangling_edges'], 1)
        self.assertEqual(report['relations']['examined_edges'], 2)
        self.assertTrue(report['relations']['complete'])
        self.assertEqual(report['unknown_table_count'], 1)
        self.assertNotIn('private_', json.dumps(report))
        self.assertNotIn('999', json.dumps(report))

    def test_unknown_envelope_not_claimed_as_valid(self):
        connection = sqlite3.connect(':memory:')
        connection.executescript('CREATE TABLE genius_metadata(genius_id INTEGER);'
                                'CREATE TABLE genius_similarities(genius_id INTEGER,version INTEGER,data BLOB);')
        connection.execute('INSERT INTO genius_similarities VALUES(1,1,?)', (b'private malformed blob',))
        connection.commit()
        report = audit.inspect_sqlite(connection.serialize())
        connection.close()
        self.assertFalse(report['relations']['complete'])
        self.assertEqual(report['relations']['unsupported_rows'], 1)
        self.assertEqual(report['relations']['seeds_missing_metadata'], 1)

    def test_paths_limits_and_encrypted_header_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'iPod_Control' / 'iTunes'
            directory.mkdir(parents=True)
            original = database(b'x' * 32)
            (directory / 'iTunesDB').write_bytes(original)
            (directory / 'iTunesSD').symlink_to(directory / 'iTunesDB')
            encrypted = bytearray(4096)
            encrypted[16:24] = bytes.fromhex('100001010c402020')
            (directory / 'Genius.itdb').write_bytes(encrypted)
            report = audit.inspect(root)
            self.assertEqual(report['files']['iTunesSD']['status'], 'symlink_rejected')
            self.assertEqual(report['files']['Genius.itdb']['status'], 'unsupported_encryption')
            self.assertEqual((directory / 'iTunesDB').read_bytes(), original)
            with patch.object(audit, 'MAX_BYTES', 10):
                self.assertEqual(audit.inspect_file(directory / 'iTunesDB')['status'], 'size_limit')
            alias = root / 'alias'
            alias.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(ValueError):
                audit.inspect(alias)

    def test_cli_refuses_source_output_and_unknown_format(self):
        script = Path(audit.__file__)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'Genius.itdb'
            source.write_bytes(b'private unknown format')
            self.assertEqual(audit.inspect_file(source)['status'], 'unsupported')
            blocked = subprocess.run([sys.executable, str(script), str(root), '--output', str(root / 'report.json')], capture_output=True)
            self.assertEqual(blocked.returncode, 2)
            self.assertFalse((root / 'report.json').exists())
            result = subprocess.run([sys.executable, str(script), str(source)], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertNotIn(b'private', result.stdout)
            self.assertEqual(source.read_bytes(), b'private unknown format')


if __name__ == '__main__':
    unittest.main()
