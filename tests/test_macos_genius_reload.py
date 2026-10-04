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
from probe_macos_genius_reload import decode_fixture_payload, menu_script, parse_query, probe, query_script, validate_fixture, generate_script, genius_playlists_script, parse_genius_playlists, try_genius_playlist


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

    def test_original_zero_ids_accepted_but_mixed_and_duplicate_rejected(self):
        document = fixture_document()
        for track in document['expected_tracks']:
            track['genius_id'] = '0000000000000000'
        validate_fixture(document)
        document['expected_tracks'][0]['genius_id'] = '0000000070000001'
        with self.assertRaisesRegex(ValueError, 'both zero'):
            validate_fixture(document)
        document['expected_tracks'][1]['genius_id'] = '0000000070000001'
        with self.assertRaisesRegex(ValueError, 'distinct'):
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

    def test_generation_only_selects_fixture_pid_and_whitelisted_menu(self):
        script = generate_script('0000000000000001')
        self.assertIn('whose persistent ID is "0000000000000001"', script)
        self.assertIn('menu item "Genius Playlist"', script)
        self.assertIn('menu item "New"', script)
        self.assertIn('if not (enabled of geniusItem) then return "DISABLED"', script)
        self.assertNotIn('Turn On', script)
        self.assertNotIn('Update', script)
        self.assertNotIn('sign', script.lower())
        with self.assertRaises(ValueError):
            generate_script('" invalid injection')

    def test_disabled_generation_does_not_query_or_claim_generation(self):
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, 'DISABLED\n', '')
        result = try_genius_playlist(fixture_document()['expected_tracks'], runner)
        self.assertEqual(result['status'], 'disabled')
        self.assertFalse(result['generation_attempted'])
        self.assertFalse(result['generation_observed'])
        self.assertEqual(len(commands), 1)

    def test_generation_result_checks_all_native_playlist_metadata(self):
        native = 'PLAYLIST_COUNT\t1\nPLAYLIST\t1\t2\nTRACK\t0000000000000001\tsynthetic-tone-1\t3\nTRACK\t0000000000000002\tsynthetic-tone-2\t3\n'
        expected = fixture_document()['expected_tracks']
        self.assertEqual(len(parse_genius_playlists(native, expected)[0]['tracks']), 2)
        for malformed in (native.replace('PLAYLIST_COUNT\t1', 'PLAYLIST_COUNT\t5'), native.replace('PLAYLIST\t1', 'PLAYLIST\t2'), native.replace('0000000000000002', 'FFFFFFFFFFFFFFFF'), native.replace('\t3\n', '\t6\n'), native + 'garbage\n'):
            with self.assertRaises(ValueError):
                parse_genius_playlists(malformed, expected)
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, native if command[-1] == genius_playlists_script() else 'CLICKED', '')
        with patch('probe_macos_genius_reload.time.sleep'):
            result = try_genius_playlist(expected, runner)
        self.assertTrue(result['generation_attempted'])
        self.assertTrue(result['generation_observed'])

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
                if command[0] == 'open':
                    bundle = home / 'Music/Music/Music Library.musiclibrary'
                    (bundle / 'Application.musicdb').write_bytes(b'synthetic support')
                    (bundle / 'Extras.itdb').write_bytes(b'synthetic extras')
                    (bundle / 'Preferences.plist').write_bytes(b'synthetic settings')
                    (bundle / 'sentinel').touch()
                return subprocess.CompletedProcess(command, 1 if command[0] == 'pgrep' else 0, '', '')
            with patch('probe_macos_genius_reload.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}), patch('probe_macos_genius_reload.Path.cwd', return_value=home), patch('probe_macos_music.dismiss_music_promotion', return_value={'status': 'not_needed'}, create=True):
                report = probe(fixture, home / 'out', runner=runner, popen=lambda *a, **kw: Process(), home=home)
            self.assertTrue(report['expected_tracks_loaded'])
            self.assertEqual(report['steps']['dismiss_music_promotion']['status'], 'not_needed')
            self.assertTrue(report['after_copy_consistent_exit'])
            self.assertFalse(report['explicit_library'])
            self.assertEqual(len(report['support_files']), 4)
            self.assertEqual((home / 'out/support-after/Application.musicdb').read_bytes(), b'synthetic support')
            self.assertFalse(report['actual_genius_generation_tested'])
            self.assertEqual(len(list((home / 'out/after').iterdir())), 3)
            self.assertFalse(any('importedTracks' in c[-1] for c in commands))
            self.assertEqual(len(list((home / 'data/macos-probe/synthetic-media').iterdir())), 2)


if __name__ == '__main__':
    unittest.main()
