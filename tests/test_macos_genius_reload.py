"""Synthetic reload fixture boundaries; native app remains an Actions-only test."""
import base64
import copy
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_macos_genius_reload import decode_fixture_payload, menu_script, parse_query, probe, query_script, validate_fixture


def fixture_document():
    hfma = bytearray(176)
    hfma[:4] = b'hfma'
    struct.pack_into('<II', hfma, 4, 160, len(hfma))
    genius = bytearray(4096)
    genius[16:24] = bytes.fromhex('100001010c402020')
    files = {'Library.musicdb': hfma, 'Library Preferences.musicdb': hfma, 'Genius.itdb': genius}
    return dict(schema_version=1, fixture_kind='synthetic-tone-only', app_template_version='1.6.6',
                files={name: dict(base64=base64.b64encode(blob).decode(), sha256=hashlib.sha256(blob).hexdigest()) for name, blob in files.items()},
                expected_tracks=[dict(persistent_id=f'{i:016X}', genius_id=f'{0x70000000+i:016X}', title=f'synthetic-tone-{i}', duration_ms=3000) for i in (1, 2)])


class ReloadTests(unittest.TestCase):
    def test_fixture_headers_and_sha_checked(self):
        document = fixture_document()
        self.assertEqual(set(validate_fixture(document)), set(document['files']))
        document['files']['Library.musicdb']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'sha256'):
            validate_fixture(document)

    def test_shape_titles_ids_size_and_base64_rejected(self):
        for mutate in (
            lambda d: d['files'].update({'../private': d['files']['Genius.itdb']}),
            lambda d: d['expected_tracks'][0].update(title='Personal song'),
            lambda d: d['expected_tracks'][0].update(genius_id=True),
            lambda d: d['expected_tracks'][0].update(duration_ms=True),
            lambda d: d['expected_tracks'][1].update(persistent_id=d['expected_tracks'][0]['persistent_id']),
            lambda d: d['files']['Genius.itdb'].update(base64='a' * 140000),
            lambda d: d['files']['Genius.itdb'].update(base64='%%%'),
        ):
            document = fixture_document()
            mutate(document)
            with self.assertRaises(ValueError):
                validate_fixture(document)

    def test_payload_bounds_trailing_stream_and_bomb(self):
        document = fixture_document()
        compressed = gzip.compress(json.dumps(document).encode())
        self.assertEqual(decode_fixture_payload(base64.b64encode(compressed).decode()), document)
        for encoded in ('%%%', 'x' * 60001, base64.b64encode(compressed + gzip.compress(b'{}')).decode(),
                        base64.b64encode(gzip.compress(b'x' * 350001)).decode(), base64.b64encode(compressed[:-5]).decode()):
            with self.assertRaises(ValueError):
                decode_fixture_payload(encoded)

    def test_query_parse_rejects_unknown_tracks_and_nonfinite_duration(self):
        good = 'COUNT\t2\nTRACK\t0000000000000001\tsynthetic-tone-1\t3.0\nTRACK\t0000000000000002\tsynthetic-tone-2\t3.0\n'
        self.assertEqual(len(parse_query(good)), 2)
        for bad in (good.replace('3.0', 'nan'), good.replace('synthetic-tone-1', 'personal'), good.replace('COUNT\t2', 'COUNT\t3'), good.replace('0000000000000002', '0000000000000001')):
            with self.assertRaises(ValueError):
                parse_query(bad)
        self.assertNotIn('click', menu_script())
        self.assertNotIn('add ', query_script())

    def test_non_actions_prevents_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out'
            with patch('probe_macos_genius_reload.platform.system', return_value='Linux'):
                with self.assertRaises(ValueError):
                    probe(Path(directory) / 'missing', output)
            self.assertFalse(output.exists())

    def test_existing_library_prevents_commands_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / 'Music/Personal.musiclibrary').mkdir(parents=True)
            fixture = home / 'fixture.json'
            fixture.write_text(json.dumps(fixture_document()))
            with patch('probe_macos_genius_reload.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                with self.assertRaisesRegex(ValueError, 'pre-existing'):
                    probe(fixture, home / 'out', home=home, runner=lambda *a, **k: self.fail('must not execute'))
            self.assertFalse((home / 'out').exists())

    def test_fresh_restore_observes_pid_and_collects_after_exit(self):
        class Process:
            returncode = 0
            def communicate(self, timeout):
                return ('COUNT\t2\nTRACK\t0000000000000001\tsynthetic-tone-1\t3\nTRACK\t0000000000000002\tsynthetic-tone-2\t3\n', '')
            def poll(self):
                return 0
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            fixture = home / 'fixture.json'
            fixture.write_text(json.dumps(fixture_document()))
            commands = []
            def runner(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 1 if command[0] == 'pgrep' else 0, '', '')
            with patch('probe_macos_genius_reload.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}), patch('probe_macos_genius_reload.Path.cwd', return_value=home):
                report = probe(fixture, home / 'out', runner=runner, popen=lambda *a, **kw: Process(), home=home)
            self.assertTrue(report['expected_tracks_loaded'])
            self.assertTrue(report['after_copy_consistent_exit'])
            self.assertFalse(report['actual_genius_generation_tested'])
            self.assertEqual(len(list((home / 'out/after').iterdir())), 3)
            self.assertFalse(any('importedTracks' in c[-1] for c in commands))
            self.assertEqual(len(list((home / 'data/macos-probe/synthetic-media').iterdir())), 2)


if __name__ == '__main__':
    unittest.main()
