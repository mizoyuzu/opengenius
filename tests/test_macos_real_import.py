"""Native real-media imports preserve explicit original-to-native PID evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_macos_real_import import parse_import, probe, real_import_script
from probe_macos_real_library import CORE

P1, P2, N1, N2 = (f'{n:016X}' for n in (1, 2, 101, 102))
MANIFEST = {'schema_version': 1, 'expected_library_track_count': 2974, 'tracks': [
    {'persistent_id': P1, 'title': 'Original title', 'relative_media_path': 'one.m4a'},
    {'persistent_id': P2, 'title': '原曲名', 'relative_media_path': 'two.m4a'}], 'seeds': [P1]}
NATIVE = f'IMPORT_LIBRARY_COUNT\t2\nIMPORTED\t{P1}\t{N1}\t200\tEnglish metadata\t/tmp/native/one.m4a\nIMPORTED\t{P2}\t{N2}\t300\t曲二\t/tmp/native/two.m4a\n'


def create_input(root):
    root.mkdir()
    (root / 'media').mkdir()
    for name in ('one.m4a', 'two.m4a'):
        (root / 'media' / name).write_bytes(b'fictional audio')
    bundle = root / 'Music Library.musiclibrary'
    bundle.mkdir()
    for name in CORE:
        (bundle / name).write_bytes(b'NOT RESTORED')
    (root / 'manifest.json').write_text(json.dumps(MANIFEST))


class RealImportTests(unittest.TestCase):
    def test_native_tags_can_differ_without_losing_original_pid_mapping(self):
        parsed = parse_import(NATIVE, MANIFEST)
        self.assertTrue(parsed['all_selected_media_imported'])
        first = parsed['tracks'][0]
        self.assertEqual((first['original_pid'], first['persistent_id'], first['title']), (P1, N1, 'English metadata'))
        self.assertEqual(first['relative_media_path'], 'one.m4a')
        self.assertEqual(first['duration_ms'], 200000)
        script = real_import_script({P1: Path('/tmp/a"b.m4a')})
        self.assertIn('a\\"b.m4a', script)
        self.assertNotIn('set name', script)
        self.assertNotIn('set location', script)
        self.assertIn('persistent ID of track i', script)
        self.assertIn('set beforePIDs to my currentLibraryPIDs()', script)
        self.assertIn('set afterPIDs to my currentLibraryPIDs()', script)
        self.assertIn('beforePIDs does not contain', script)
        self.assertIn('if (count of newPIDs) is not 1', script)
        self.assertIn('on error errorText number errorNumber', script)
        self.assertIn('set nativeDuration to (get duration of t)', script)
        self.assertIn('set nativeLocation to (get location of t)', script)
        self.assertIn('IMPORT_LOCATION_FAILED', script)
        self.assertIn('sanitizedDiagnostic', script)
        self.assertNotIn('addedResult', script)

    def test_partial_import_is_reported_and_ambiguous_native_ids_rejected(self):
        partial = f'IMPORT_LIBRARY_COUNT\t1\nIMPORTED\t{P1}\t{N1}\t200\tEnglish metadata\t/tmp/native/one.m4a\nIMPORT_FAILED\t{P2}\n'
        parsed = parse_import(partial, MANIFEST)
        self.assertFalse(parsed['all_selected_media_imported'])
        self.assertEqual(parsed['tracks'][1]['status'], 'failed')
        diagnostics = parse_import(f'IMPORT_LIBRARY_COUNT\t2\nIMPORT_FAILED\t{P1}\t-1728\nIMPORT_FAILED\t{P2}\t-2700\n', MANIFEST)
        self.assertEqual(diagnostics['native_tracks_without_mapping'], 2)
        self.assertEqual(diagnostics['tracks'][0]['native_error_number'], -1728)
        self.assertFalse(diagnostics['all_selected_media_imported'])
        staged = parse_import(f'IMPORT_LIBRARY_COUNT\t2\nIMPORT_FAILED\t{P1}\t-1700\tgetduration\tCannot coerce value\nIMPORT_FAILED\t{P2}\t-1700\tgetname\tCannot get name\n', MANIFEST)
        self.assertEqual(staged['tracks'][0]['native_error_stage'], 'getduration')
        self.assertEqual(staged['tracks'][0]['native_error_text'], 'Cannot coerce value')
        optional_location = NATIVE.replace(f'IMPORTED\t{P1}', f'IMPORT_LOCATION_FAILED\t{P1}\t-1700\tcoerceLocation\tCannot coerce missing value\nIMPORTED\t{P1}').replace('/tmp/native/one.m4a', '-')
        mapped = parse_import(optional_location, MANIFEST)
        self.assertTrue(mapped['all_selected_media_imported'])
        self.assertIsNone(mapped['tracks'][0]['native_location'])
        self.assertEqual(mapped['tracks'][0]['location_diagnostic']['native_error_stage'], 'coerceLocation')
        for bad in (NATIVE.replace(N2, N1), NATIVE.replace('\t200\t', '\tnan\t'), NATIVE.replace('IMPORT_LIBRARY_COUNT\t2', 'IMPORT_LIBRARY_COUNT\t1'), NATIVE.replace(P2, 'FFFFFFFFFFFFFFFF')):
            with self.assertRaises(ValueError):
                parse_import(bad, MANIFEST)

    def test_non_macos_and_existing_library_prevent_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input'
            create_input(source)
            with patch('probe_macos_real_import.platform.system', return_value='Linux'):
                with self.assertRaises(ValueError):
                    probe(source, root / 'out')
            home = root / 'home'
            (home / 'Music/Existing.musiclibrary').mkdir(parents=True)
            with patch('probe_macos_real_import.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                with self.assertRaisesRegex(ValueError, 'pre-existing'):
                    probe(source, root / 'out', home=home, runner=lambda *a, **kw: self.fail('must not execute'))
            self.assertFalse((root / 'out').exists())

    def test_fresh_native_bundle_and_playback_are_collected_without_restoring_supplied_db(self):
        class Process:
            returncode = 0
            def communicate(self, timeout):
                return NATIVE, ''
            def poll(self):
                return 0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input'
            create_input(source)
            home = root / 'home'
            commands = []
            def runner(command, **kwargs):
                commands.append(command)
                stdout = ''
                if command[0] == 'open':
                    fresh = home / 'Music/Music/Music Library.musiclibrary'
                    fresh.mkdir(parents=True)
                    for name in CORE:
                        (fresh / name).write_bytes(b'NEW NATIVE LIBRARY')
                if command[0] == 'osascript':
                    if 'LIBRARY_COUNT' in command[-1]:
                        stdout = f'LIBRARY_COUNT\t2\nLIBRARY_PID\t{N1}\nLIBRARY_PID\t{N2}\nSELECTED\t{N1}\t200\tEnglish metadata\nSELECTED\t{N2}\t300\t曲二\n'
                    elif 'PLAYBACK' in command[-1]:
                        stdout = f'PLAYBACK\t{N1}\tplaying\t2\t{N1}\tplaying\t4'
                return subprocess.CompletedProcess(command, 1 if command[0] == 'pgrep' else 0, stdout, '')
            with patch('probe_macos_real_import.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}), patch('probe_macos_real_import.dismiss_music_promotion', return_value={'status': 'not_needed'}):
                report = probe(source, root / 'out', home=home, runner=runner, popen=lambda *a, **kw: Process())
            self.assertEqual(report['status'], 'native_import_verified')
            self.assertFalse(report['supplied_bundle_restored'])
            self.assertFalse(report['replacement_genius_relations_installed'])
            self.assertTrue(report['seed_results'][0]['playback']['position_advanced'])
            self.assertTrue(report['music_exited'])
            self.assertEqual((root / 'out/after/Library.musicdb').read_bytes(), b'NEW NATIVE LIBRARY')
            self.assertEqual((source / 'Music Library.musiclibrary/Library.musicdb').read_bytes(), b'NOT RESTORED')
            self.assertEqual(json.loads((root / 'out/native-mapping.json').read_text())['tracks'][0]['original_pid'], P1)
            self.assertFalse(any('set location' in c[-1] or 'click geniusItem' in c[-1] for c in commands))


if __name__ == '__main__':
    unittest.main()
