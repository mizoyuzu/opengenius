"""Test macOS probe isolation, bounded commands, and real synthetic PCM data."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_macos_music import bootstrap_music, bootstrap_script, collect_libraries, dismiss_music_promotion, import_script, make_tone, probe, run_step, tone_frequency, validate_track_count


class FinishedProcess:
    returncode = 0

    def communicate(self, timeout):
        return ('synthetic import complete, 2', '')

    def poll(self):
        return self.returncode


class MacOSProbeTests(unittest.TestCase):
    def test_ocr_promotion_requires_unique_known_title_and_not_now(self):
        known = [{'text': 'Hear About New Music First', 'x': 500, 'y': 380},
                 {'text': 'Not Now', 'x': 325, 'y': 671}]
        for rows, should_click in ((known, True), (known[1:], False),
                                   (known + [known[1]], False),
                                   ([known[0], {**known[1], 'x': float('nan')}], False)):
            commands = []
            def runner(command, **kwargs):
                commands.append(command)
                stdout = json.dumps(rows) if command[0] == 'swift' else ''
                return subprocess.CompletedProcess(command, 0, stdout, '')
            with tempfile.TemporaryDirectory() as directory:
                result = dismiss_music_promotion(Path(directory), runner)
            clicks = [c for c in commands if c[0] == 'osascript']
            self.assertEqual(bool(clicks), should_click)
            if should_click:
                self.assertIn('frontmost', clicks[0][-1])
                self.assertEqual(commands[-1][-2:], ['325', '671'])
                self.assertEqual(result['status'], 'ok')
            else:
                self.assertEqual(result['status'], 'skipped')

    def test_tone_has_expected_duration_amplitude_and_fades(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tone.wav'
            make_tone(path, 440, seconds=.2, sample_rate=8000)
            with wave.open(str(path)) as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()), (1, 2, 8000, 1600))
                samples = struct.unpack('<1600h', audio.readframes(1600))
            self.assertEqual(samples[0], 0)
            self.assertEqual(samples[-1], 0)
            self.assertGreater(max(samples), 4000)
            self.assertLessEqual(max(map(abs, samples)), 4096)

    def test_track_count_and_tone_indices_have_strict_boundaries(self):
        for valid in (2, 128):
            self.assertEqual(validate_track_count(valid), valid)
        for invalid in (True, 1, 129, '2', 2.0):
            with self.assertRaises(ValueError):
                validate_track_count(invalid)
        frequencies = [tone_frequency(i) for i in range(1, 129)]
        self.assertEqual(frequencies[:2], [440, 660])
        self.assertEqual(len(set(frequencies)), 128)
        self.assertLess(max(frequencies), 22050 / 2)
        for invalid in (True, 0, 129):
            with self.assertRaises(ValueError):
                tone_frequency(invalid)

    def test_large_import_is_one_bounded_batch_with_filename_titles(self):
        paths = [Path(f'/tmp/synthetic-tone-{i}.wav') for i in range(1, 129)]
        script = import_script(paths)
        self.assertEqual(script.count('set importedTracks to add'), 1)
        self.assertEqual(script.count('(POSIX file'), 128)
        self.assertIn('synthetic-tone-128.wav', script)
        self.assertNotIn('repeat with importedTrack', script)
        with self.assertRaises(ValueError):
            import_script(paths + [Path('/tmp/extra.wav')])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out'
            with self.assertRaises(ValueError):
                probe(output, track_count=129)
            self.assertFalse(output.exists())

    def test_timeout_is_evidence_and_does_not_retain_partial_output(self):
        def runner(command, **kwargs):
            self.assertEqual(kwargs['timeout'], 7)
            raise subprocess.TimeoutExpired(command, 7, output='partial output')
        result = run_step(['osascript'], 7, runner)
        self.assertEqual(result['status'], 'timeout')
        self.assertNotIn('stdout', result)

    def test_import_paths_are_applescript_escaped_and_metadata_synthetic(self):
        script = import_script([Path('/tmp/a"b\\c.wav')])
        self.assertIn('a\\"b\\\\c.wav', script)
        self.assertIn('set importedTracks to add {(POSIX file', script)
        self.assertNotIn('set name', script)
        self.assertNotIn('set artist', script)
        self.assertIn('with timeout of 150 seconds', script)
        self.assertIn('count of tracks of library playlist 1', script)

    def test_only_new_library_databases_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / 'Music'
            old = music / 'Personal.musiclibrary'
            new = music / 'Music/Music Library.musiclibrary'
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / 'Library.musicdb').write_bytes(b'private')
            (new / 'Library.musicdb').write_bytes(b'new')
            (new / 'Genius.itdb').write_bytes(b'genius')
            (new / 'Library Preferences.musicdb').write_bytes(b'fresh preferences')
            (new / 'Preferences.plist').write_bytes(b'synthetic bundle support')
            (new / 'oversized-media.wav').write_bytes(b'0' * (1024 * 1024 + 1))
            output = root / 'out'
            output.mkdir()
            records = collect_libraries(music, output, {old})
            copied = list((output / 'generated-library').rglob('*'))
            self.assertEqual(sorted(path.name for path in copied if path.is_file()), ['Genius.itdb', 'Library Preferences.musicdb', 'Library.musicdb', 'Preferences.plist'])
            old_record = next(record for record in records if not record['created_by_probe'])
            self.assertNotIn('sha256', old_record['files'][0])
            new_record = next(record for record in records if record['created_by_probe'])
            large = next(record for record in new_record['files'] if record['path'] == 'oversized-media.wav')
            self.assertNotIn('sha256', large)
            self.assertNotIn('copy', large)

    def test_existing_library_prevents_launch_import_and_screenshot(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / 'Music/Private.musiclibrary').mkdir(parents=True)
            commands = []
            def runner(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('probe_macos_music.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                result = probe(home / 'output', runner=runner, popen=lambda *a, **kw: FinishedProcess(), home=home)
            self.assertEqual(result['import_status'], 'skipped_existing_library')
            self.assertFalse(any(command[0] in ('open', 'osascript', 'screencapture') for command in commands))
            self.assertTrue((home / 'output/report.json').is_file())

    def test_non_macos_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            with patch('probe_macos_music.platform.system', return_value='Linux'):
                with self.assertRaisesRegex(ValueError, 'requires macOS'):
                    probe(output)
            self.assertFalse(output.exists())

    def test_real_mac_outside_actions_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            with patch('probe_macos_music.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'false'}):
                with self.assertRaisesRegex(ValueError, 'GitHub Actions runner'):
                    probe(output)
            self.assertFalse(output.exists())

    def test_fresh_import_records_ui_failure_and_copies_flushed_database(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            commands = []
            def runner(command, **kwargs):
                commands.append(command)
                if command[0] == 'osascript' and 'importedTracks' in command[-1]:
                    bundle = home / 'Music/Music/Music Library.musiclibrary'
                    bundle.mkdir(parents=True)
                    (bundle / 'Library.musicdb').write_bytes(b'created db')
                if command[0] == 'osascript' and 'System Events' in command[-1]:
                    return subprocess.CompletedProcess(command, 1, '', 'UI permission unavailable')
                if command[0] == 'osascript' and 'to quit' in command[-1]:
                    (home / 'Music/Music/Music Library.musiclibrary/Library.musicdb').write_bytes(b'flushed db')
                self.assertLessEqual(kwargs['timeout'], 180)
                return subprocess.CompletedProcess(command, 1 if command[0] == 'pgrep' else 0, '', '')
            def popen(command, **kwargs):
                runner(command, timeout=90)
                return FinishedProcess()
            with patch('probe_macos_music.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                result = probe(home / 'output', runner=runner, popen=popen, home=home)
            self.assertEqual(result['import_status'], 'ok')
            self.assertEqual(result['track_count'], 2)
            self.assertTrue(result['music_exited'])
            self.assertTrue(result['after_copy_consistent_exit'])
            self.assertEqual(result['probe_network_requests'], 0)
            self.assertFalse(result['music_app_network_activity_observed'])
            self.assertNotIn('network_requests', result)
            self.assertEqual(result['steps']['music_windows']['status'], 'failed')
            self.assertEqual(len(result['libraries']), 1)
            copied_db = next((home / 'output/generated-library').rglob('Library.musicdb'))
            self.assertEqual(copied_db.read_bytes(), b'flushed db')
            self.assertEqual(len(list((home / 'output/synthetic-media').glob('*.wav'))), 2)
            self.assertTrue(any(command[0] == 'screencapture' for command in commands))
            self.assertEqual(sum(command[0] == 'osascript' and 'importedTracks' in command[-1] for command in commands), 1)

    def test_probe_generates_configured_128_tracks_with_shared_frequencies(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            triggered = []
            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 1 if command[0] == 'pgrep' else 0, '', '')
            def popen(command, **kwargs):
                triggered.append(command[-1])
                return FinishedProcess()
            with patch('probe_macos_music.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}), patch('probe_macos_music.make_tone') as make_audio, patch('probe_macos_music.dismiss_music_promotion', return_value={'status': 'not_needed'}):
                result = probe(home / 'out', runner=runner, popen=popen, home=home, track_count=128)
            self.assertEqual(result['track_count'], 128)
            self.assertEqual(make_audio.call_count, 128)
            self.assertEqual(make_audio.call_args_list[-1].args, ((home / 'out/synthetic-media/synthetic-tone-128.wav').resolve(), tone_frequency(128)))
            self.assertEqual(triggered[0].count('(POSIX file'), 128)

    def test_bootstrap_permission_whitelist_and_bounded_loop(self):
        script = bootstrap_script()
        self.assertIn('(dialogText contains "hosted-compute-agent") and (dialogText contains "Music")', script)
        self.assertIn('if matchingPermission and buttonName is "Allow" then', script)
        self.assertIn('processName is "Music" and buttonName is "Start Listening"', script)
        self.assertIn('>= 20 then exit repeat', script)
        self.assertIn('(count of observedButtons) < 30', script)
        self.assertIn('processName is "Music" and dialogText contains "Hear About New Music First"', script)
        self.assertIn('is "Not Now" then set isNotNow to true', script)
        self.assertIn('itemRole is in {"AXButton", "AXLink", "AXStaticText"}', script)
        self.assertIn('perform action "AXPress" of uiItem', script)
        self.assertIn('{"AXTitle", "AXDescription", "AXValue"}', script)
        self.assertNotIn('is "Continue"', script)
        self.assertIn('with timeout of 2 seconds', script)
        self.assertNotIn('in application processes', script)

    def test_bootstrap_trigger_is_running_during_ui_probe(self):
        events = []
        def popen(command, **kwargs):
            self.assertIn('set importedTracks to add', command[-1])
            events.append('trigger')
            return FinishedProcess()
        def runner(command, **kwargs):
            self.assertEqual(events, ['trigger'])
            self.assertEqual(kwargs['timeout'], 30)
            events.append('ui')
            return subprocess.CompletedProcess(command, 1, '', 'permission unavailable')
        result = bootstrap_music([Path('/tmp/tone.wav')], runner, popen)
        self.assertEqual(result['bootstrap_ui']['status'], 'failed')
        self.assertEqual(result['import_synthetic_audio']['status'], 'ok')

    def test_bootstrap_kills_blocked_trigger(self):
        class BlockedProcess(FinishedProcess):
            returncode = None
            killed = False

            def communicate(self, timeout):
                if not self.killed:
                    raise subprocess.TimeoutExpired('osascript', timeout)
                return ('', '')

            def kill(self):
                self.killed = True
                self.returncode = -9
        process = BlockedProcess()
        result = bootstrap_music([Path('/tmp/tone.wav')], lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''), lambda *a, **kw: process)
        self.assertTrue(process.killed)
        self.assertEqual(result['import_synthetic_audio']['status'], 'timeout')


if __name__ == '__main__':
    unittest.main()
