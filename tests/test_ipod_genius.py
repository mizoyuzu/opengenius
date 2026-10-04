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
