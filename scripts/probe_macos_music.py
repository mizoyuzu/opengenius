#!/usr/bin/env python3
"""Probe Music on a disposable macOS runner using only generated audio.

No account login, network request, or supplied library is needed. Existing Music
libraries prevent launching/importing so this probe cannot modify a personal one.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import time
import wave


def make_tone(path, frequency, seconds=3, sample_rate=22050):
    """Write a quiet mono PCM tone with fades, suitable for Music import."""
    count = int(seconds * sample_rate)
    if count <= 0 or sample_rate <= 0 or not 0 < frequency < sample_rate / 2:
        raise ValueError('invalid tone parameters')
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        fade = max(1, int(sample_rate * .05))
        frames = bytearray()
        for i in range(count):
            envelope = min(1, i / fade, (count - 1 - i) / fade)
            value = round(4096 * envelope * math.sin(2 * math.pi * frequency * i / sample_rate))
            frames.extend(struct.pack('<h', value))
        audio.writeframes(frames)


def run_step(command, timeout=20, runner=subprocess.run):
    """Bound execution and output; command failures are probe evidence."""
    started = time.monotonic()
    result = {'command': list(command), 'timeout_seconds': timeout}
    try:
        done = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
        result.update(status='ok' if done.returncode == 0 else 'failed',
                      returncode=done.returncode,
                      stdout=(done.stdout or '')[:12000], stderr=(done.stderr or '')[:12000])
    except subprocess.TimeoutExpired:
        result.update(status='timeout')
    except OSError as error:
        result.update(status='unavailable', error_type=type(error).__name__)
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return result


def apple_string(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def import_script(paths):
    lines = ['with timeout of 55 seconds', 'tell application "Music"']
    for index, path in enumerate(paths, 1):
        lines.extend([
            f'set importedTracks to add (POSIX file {apple_string(path)})',
            'repeat with importedTrack in importedTracks',
            f'set name of importedTrack to "Synthetic Tone {index}"',
            'set artist of importedTrack to "OpenGenius Probe"',
            'set album of importedTrack to "Synthetic Probe"',
            'end repeat',
        ])
    lines.extend(['return {"synthetic import complete", count of tracks of library playlist 1}', 'end tell', 'end timeout'])
    return '\n'.join(lines)


def bootstrap_script():
    """Click only the observed Music automation prompt and welcome button."""
    return '''with timeout of 22 seconds
set observedButtons to {}
set actionsTaken to {}
set startedAt to current date
tell application "System Events"
repeat 36 times
    if ((current date) - startedAt) >= 20 then exit repeat
    repeat with appProcess in application processes
        try
            set processName to name of appProcess
            set processWindows to windows of appProcess
            repeat with w in processWindows
                set dialogText to ""
                set uiItems to entire contents of w
                repeat with uiItem in uiItems
                    try
                        if class of uiItem is static text then set dialogText to dialogText & " " & (value of uiItem as text)
                    end try
                end repeat
                set matchingPermission to (dialogText contains "hosted-compute-agent") and (dialogText contains "Music")
                repeat with uiItem in uiItems
                    try
                        if class of uiItem is button then
                            set buttonName to name of uiItem as text
                            set buttonLabel to processName & ": " & buttonName
                            if (count of observedButtons) < 30 and observedButtons does not contain buttonLabel then set end of observedButtons to buttonLabel
                            if matchingPermission and buttonName is "Allow" then
                                click uiItem
                                set end of actionsTaken to "allowed hosted-compute-agent to control Music"
                            else if processName is "Music" and buttonName is "Start Listening" then
                                click uiItem
                                set end of actionsTaken to "dismissed Music welcome"
                            end if
                        end if
                    end try
                end repeat
            end repeat
        end try
    end repeat
    delay 0.5
end repeat
end tell
return {actionsTaken, observedButtons}
end timeout'''


def bootstrap_music(runner=subprocess.run, popen=subprocess.Popen):
    """Trigger Music automation concurrently so the permission UI can appear."""
    trigger_command = ['osascript', '-e', 'with timeout of 30 seconds\ntell application "Music" to return version\nend timeout']
    results = {}
    try:
        process = popen(trigger_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        return {'automation_trigger': {'status': 'unavailable', 'error_type': type(error).__name__}}
    try:
        results['bootstrap_ui'] = run_step(['osascript', '-e', bootstrap_script()], 25, runner)
        try:
            stdout, stderr = process.communicate(timeout=5)
            results['automation_trigger'] = {'status': 'ok' if process.returncode == 0 else 'failed',
                                              'returncode': process.returncode,
                                              'stdout': (stdout or '')[:12000], 'stderr': (stderr or '')[:12000]}
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            results['automation_trigger'] = {'status': 'timeout'}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    return results


def library_roots(music_directory):
    if not music_directory.exists():
        return []
    return sorted(path for path in music_directory.rglob('*.musiclibrary')
                  if path.is_dir() and not path.is_symlink())


def collect_libraries(music_directory, output, preexisting):
    """Inventory bundle files and copy DBs only from newly created bundles."""
    records = []
    for root in library_roots(music_directory):
        fresh = root not in preexisting
        bundle_record = {'path': str(root.relative_to(music_directory)),
                         'created_by_probe': fresh, 'files': []}
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            info = {'path': str(path.relative_to(root)), 'size': path.stat().st_size}
            # Large media never need to be read, hashed, or copied here.
            if path.name in ('Library.musicdb', 'Genius.itdb', 'Library Preferences.musicdb'):
                if fresh:
                    info['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                    destination = output / 'generated-library' / root.relative_to(music_directory) / path.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
                    info['copy'] = str(destination.relative_to(output))
            bundle_record['files'].append(info)
        records.append(bundle_record)
    return records


def probe(output, *, runner=subprocess.run, popen=subprocess.Popen, home=None):
    if platform.system() != 'Darwin':
        raise ValueError('this probe requires macOS')
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('this probe requires a disposable GitHub Actions runner')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    music_directory = (Path(home) if home else Path.home()) / 'Music'
    preexisting = set(library_roots(music_directory))
    report = {'schema_version': 1, 'synthetic_audio_only': True,
              'probe_network_requests': 0, 'music_app_network_activity_observed': False,
              'platform': platform.platform(),
              'architecture': platform.machine(), 'steps': {}, 'libraries': []}
    steps = report['steps']
    def step(name, command, timeout=20):
        steps[name] = run_step(command, timeout, runner)
    try:
        step('os', ['sw_vers'])
        step('gui_session', ['stat', '-f', '%Su', '/dev/console'])
        app = Path('/System/Applications/Music.app')
        step('music_version', ['/usr/libexec/PlistBuddy', '-c', 'Print :CFBundleShortVersionString', str(app / 'Contents/Info.plist')])
        step('music_build', ['/usr/libexec/PlistBuddy', '-c', 'Print :CFBundleVersion', str(app / 'Contents/Info.plist')])
        step('music_entitlements', ['codesign', '-d', '--entitlements', ':-', str(app)])
        if preexisting:
            report['import_status'] = 'skipped_existing_library'
            report['skip_reason'] = 'preexisting_library'
            report['preexisting_library_count'] = len(preexisting)
        else:
            audio_directory = output / 'synthetic-media'
            audio_directory.mkdir()
            tones = [audio_directory / f'synthetic-tone-{index}.wav' for index in (1, 2)]
            for path, frequency in zip(tones, (440, 660)):
                make_tone(path, frequency)
            step('launch_music', ['open', '-a', str(app)])
            steps.update(bootstrap_music(runner, popen))
            step('import_synthetic_audio', ['osascript', '-e', import_script(tones)], 60)
            report['import_status'] = steps['import_synthetic_audio']['status']
            # Ask Music to quit so copied DBs are more likely to be flushed.
            step('music_windows', ['osascript', '-e', 'with timeout of 10 seconds\ntell application "System Events"\nreturn {UI elements enabled, count of windows of process "Music"}\nend tell\nend timeout'], 15)
            step('screenshot', ['screencapture', '-x', str(output / 'music-screen.png')], 15)
            step('quit_music', ['osascript', '-e', 'with timeout of 15 seconds\ntell application "Music" to quit\nend timeout'], 20)
            report['libraries'] = collect_libraries(music_directory, output, preexisting)
        report['actual_genius_generation_tested'] = False
    finally:
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='new output directory')
    args = parser.parse_args()
    try:
        report = probe(args.output)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({'output': str(args.output), 'import_status': report['import_status'],
                      'generated_libraries': len(report['libraries'])}))


if __name__ == '__main__':
    main()
