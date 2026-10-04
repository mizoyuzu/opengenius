"""Private native harness boundaries and observations; real execution is macOS-only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_macos_real_library import (CORE, location_writable, parse_links, parse_playback, parse_playlists,
                                    parse_selected_query, playback_script, probe, relink_script, selected_query_script, validate_inputs)

P1, P2 = '0000000000000001', '0000000000000002'
SDEF = '<dictionary><suite><class name="file track"><property name="location"/></class></suite></dictionary>'
NATIVE = f'LIBRARY_COUNT\t2\nLIBRARY_PID\t{P1}\nLIBRARY_PID\t{P2}\nSELECTED\t{P1}\t200\tPrivate Song\nSELECTED\t{P2}\t300\t曲二\n'


def create_input(root):
    root.mkdir()
    (root / 'media').mkdir()
    for name in ('one.m4a', 'two.m4a'):
        (root / 'media' / name).write_bytes(b'fictional test audio')
    bundle = root / 'Music Library.musiclibrary'
    bundle.mkdir()
    for name in CORE:
        (bundle / name).write_bytes(b'fixture database')
    manifest = {'schema_version': 1, 'expected_library_track_count': 2, 'tracks': [
        {'persistent_id': P1, 'title': 'Private Song', 'relative_media_path': 'one.m4a'},
        {'persistent_id': P2, 'title': '曲二', 'relative_media_path': 'two.m4a'}], 'seeds': [P1]}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


class NativeRealTests(unittest.TestCase):
    def test_manifest_rejects_outside_media_symlink_and_duplicate_seeds(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input'
            manifest = create_input(source)
            validate_inputs(source)
            manifest['tracks'][0]['relative_media_path'] = '../manifest.json'
            (source / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'inside'):
                validate_inputs(source)
            manifest['tracks'][0]['relative_media_path'] = 'one.m4a'
            manifest['seeds'] = [P1, P1]
            (source / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'unique'):
                validate_inputs(source)
            manifest['seeds'] = [P1]
            (source / 'manifest.json').write_text(json.dumps(manifest))
            (source / 'media/one.m4a').unlink()
            (source / 'media/one.m4a').symlink_to(source / 'manifest.json')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                validate_inputs(source)

    def test_native_count_pid_title_verification_rejects_duplicate_unknown_and_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = create_input(Path(directory) / 'input')
            result = parse_selected_query(NATIVE, manifest)
            self.assertTrue(result['expected_library_count_loaded'])
            self.assertTrue(result['selected_tracks_loaded'])
            wrong_title = parse_selected_query(NATIVE.replace('Private Song', 'Different'), manifest)
            self.assertFalse(wrong_title['selected_tracks_loaded'])
            for bad in (NATIVE.replace('LIBRARY_COUNT\t2', 'LIBRARY_COUNT\t3'), NATIVE.replace(f'LIBRARY_PID\t{P2}', f'LIBRARY_PID\t{P1}'), NATIVE.replace('\t200\t', '\tnan\t')):
                with self.assertRaises(ValueError):
                    parse_selected_query(bad, manifest)
            self.assertNotIn('add ', selected_query_script(manifest['tracks']))
            self.assertIn('if nativeCount is 0 then return outputText', selected_query_script(manifest['tracks']))
            empty = parse_selected_query('LIBRARY_COUNT\t0\n', manifest)
            self.assertFalse(empty['expected_library_count_loaded'])
            self.assertFalse(empty['selected_tracks_loaded'])

    def test_relink_only_selected_file_tracks_after_sdef_writability(self):
        self.assertTrue(location_writable(SDEF))
        self.assertFalse(location_writable(SDEF.replace('name="location"', 'name="location" access="r"')))
        paths = {P1: Path('/tmp/private"file.m4a')}
        script = relink_script(paths)
        self.assertIn('every file track', script)
        self.assertIn('set location of t', script)
        self.assertIn('private\\"file.m4a', script)
        self.assertNotIn('add ', script)
        self.assertTrue(parse_links(f'LINK\t{P1}\tOK\t/tmp/private"file.m4a\n', paths)[0]['location_verified'])
        self.assertFalse(parse_links(f'LINK\t{P1}\tOK\t/tmp/wrong.m4a\n', paths)[0]['location_verified'])
        with self.assertRaises(ValueError):
            parse_links('', paths)

    def test_playback_requires_same_seed_playing_and_position_progress(self):
        native = f'PLAYBACK\t{P1}\tplaying\t2\t{P1}\tplaying\t4'
        self.assertTrue(parse_playback(native, P1)['position_advanced'])
        self.assertFalse(parse_playback(native.replace('\t4', '\t2'), P1)['position_advanced'])
        self.assertFalse(parse_playback(native.replace('playing', 'stopped'), P1)['position_advanced'])
        with self.assertRaises(ValueError):
            parse_playback(native.replace('\t4', '\tnan'), P1)
        self.assertIn('once true', playback_script(P1))
        self.assertIn('stop', playback_script(P1))

    def test_genius_playlists_are_bounded_and_only_known_library_pids(self):
        text = f'GENIUS_COUNT\t1\nGENIUS_PLAYLIST\t{P2}\t1\nGENIUS_TRACK\t{P2}\t{P1}\tPrivate Song\t200\n'
        self.assertEqual(parse_playlists(text, {P1})[0]['tracks'][0]['persistent_id'], P1)
        for bad in (text.replace('\t200', '\tnan'), text.replace('GENIUS_COUNT\t1', 'GENIUS_COUNT\t33'), text.replace(f'GENIUS_TRACK\t{P2}\t{P1}', f'GENIUS_TRACK\t{P2}\tFFFFFFFFFFFFFFFF')):
            with self.assertRaises(ValueError):
                parse_playlists(bad, {P1})

    def test_non_macos_and_existing_library_create_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'input'
            create_input(source)
            with patch('probe_macos_real_library.platform.system', return_value='Linux'):
                with self.assertRaises(ValueError):
                    probe(source, base / 'out')
            home = base / 'home'
            (home / 'Music/Existing.musiclibrary').mkdir(parents=True)
            with patch('probe_macos_real_library.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                with self.assertRaisesRegex(ValueError, 'pre-existing'):
                    probe(source, base / 'out', home=home, runner=lambda *a, **k: self.fail('must not execute'))
            self.assertFalse((base / 'out').exists())

    def test_integrated_load_relink_playback_disabled_menu_and_exit_copy(self):
        class Process:
            returncode = 0
            def communicate(self, timeout):
                return NATIVE, ''
            def poll(self):
                return 0
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'input'
            create_input(source)
            commands = []
            def runner(command, **kwargs):
                commands.append(command)
                output = ''
                if command[0] == 'osascript':
                    script = command[-1]
                    if 'set location of t' in script:
                        output = f'LINK\t{P1}\tOK\t{source}/media/one.m4a\nLINK\t{P2}\tOK\t{source}/media/two.m4a\n'
                    elif 'set geniusLists' in script:
                        output = 'GENIUS_COUNT\t0\n'
                    elif 'PLAYBACK' in script:
                        output = f'PLAYBACK\t{P1}\tplaying\t2\t{P1}\tplaying\t4'
                    elif 'geniusItem' in script:
                        output = 'DISABLED\n'
                return subprocess.CompletedProcess(command, 1 if command[0] == 'pgrep' else 0, output, '')
            def fake_is_file(path):
                return str(path).endswith('com.apple.Music.sdef') or Path.exists(path) and not Path.is_dir(path)
            original_read = Path.read_text
            def fake_read(path, *args, **kwargs):
                return SDEF if str(path).endswith('com.apple.Music.sdef') else original_read(path, *args, **kwargs)
            with patch('probe_macos_real_library.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}), patch('probe_macos_real_library.dismiss_music_promotion', return_value={'status': 'not_needed'}), patch.object(Path, 'is_file', fake_is_file), patch.object(Path, 'read_text', fake_read):
                result = probe(source, base / 'out', home=base / 'home', runner=runner, popen=lambda *a, **kw: Process())
            self.assertEqual(result['status'], 'library_loaded')
            restored = base / 'home/Music/Music/Music Library.musiclibrary'
            self.assertEqual(result['restored_bundle_path'], str(restored))
            self.assertTrue(restored.is_dir())
            launch = next(c for c in commands if c[0] == 'open')
            self.assertEqual(launch[-1], str(restored))
            self.assertTrue(result['music_library_locations'][0]['is_restored_bundle'])
            self.assertTrue(result['seed_results'][0]['playback']['position_advanced'])
            self.assertEqual(result['seed_results'][0]['genius_menu_status'], 'disabled')
            self.assertFalse(result['actual_genius_generation_tested'])
            self.assertTrue(result['music_exited'])
            for stage in ('before', 'after'):
                self.assertEqual({p.name for p in (base / 'out' / stage).iterdir()}, set(CORE))
            self.assertEqual((source / 'Music Library.musiclibrary/Library.musicdb').read_bytes(), b'fixture database')
            self.assertFalse(any('Turn On' in c[-1] or 'Update Genius' in c[-1] for c in commands))


if __name__ == '__main__':
    unittest.main()
