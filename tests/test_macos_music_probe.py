"""Test macOS probe isolation, bounded commands, and real synthetic PCM data."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_macos_music import bootstrap_music, bootstrap_script, collect_libraries, import_script, make_tone, probe, run_step


class FinishedProcess:
    returncode = 0

    def communicate(self, timeout):
        return ('synthetic import complete, 2', '')

    def poll(self):
        return self.returncode


class MacOSProbeTests(unittest.TestCase):
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
        self.assertIn('Synthetic Tone 1', script)
        self.assertIn('with timeout of 85 seconds', script)
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
            (new / 'Preferences.plist').write_bytes(b'do not copy')
            output = root / 'out'
            output.mkdir()
            records = collect_libraries(music, output, {old})
            copied = list((output / 'generated-library').rglob('*'))
            self.assertEqual(sorted(path.name for path in copied if path.is_file()), ['Genius.itdb', 'Library Preferences.musicdb', 'Library.musicdb'])
            old_record = next(record for record in records if not record['created_by_probe'])
            self.assertNotIn('sha256', old_record['files'][0])

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
                self.assertLessEqual(kwargs['timeout'], 90)
                return subprocess.CompletedProcess(command, 0, '', '')
            def popen(command, **kwargs):
                runner(command, timeout=90)
                return FinishedProcess()
            with patch('probe_macos_music.platform.system', return_value='Darwin'), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                result = probe(home / 'output', runner=runner, popen=popen, home=home)
            self.assertEqual(result['import_status'], 'ok')
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

    def test_bootstrap_permission_whitelist_and_bounded_loop(self):
        script = bootstrap_script()
        self.assertIn('(dialogText contains "hosted-compute-agent") and (dialogText contains "Music")', script)
        self.assertIn('if matchingPermission and buttonName is "Allow" then', script)
        self.assertIn('processName is "Music" and buttonName is "Start Listening"', script)
        self.assertIn('>= 20 then exit repeat', script)
        self.assertIn('(count of observedButtons) < 30', script)
        self.assertIn('dialogText contains "Hear About New Music First" and buttonName is "Not Now"', script)
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
