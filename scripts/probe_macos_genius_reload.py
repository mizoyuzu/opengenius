#!/usr/bin/env python3
"""Restore a bounded synthetic Music fixture on a fresh Actions macOS runner.

Only queries library metadata and Genius menu availability. No account login,
Genius enable action, audio import, key output, or probe network request.
"""
import argparse
import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import time
import zlib

from probe_macos_music import bootstrap_script, make_tone, run_step

FILES = frozenset(('Library.musicdb', 'Genius.itdb', 'Library Preferences.musicdb'))
MAX_FILE = 100 * 1024
MAX_DOCUMENT = 420 * 1024
PID = re.compile(r'[0-9A-F]{16}\Z')


def validate_fixture(document):
    if not isinstance(document, dict) or set(document) != {'schema_version', 'files', 'expected_tracks', 'app_template_version', 'fixture_kind'}:
        raise ValueError('unsupported fixture document')
    if type(document['schema_version']) is not int or document['schema_version'] != 1 or document['fixture_kind'] != 'synthetic-tone-only':
        raise ValueError('only schema 1 synthetic-tone-only fixtures are supported')
    if not isinstance(document['app_template_version'], str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', document['app_template_version']):
        raise ValueError('invalid app template version')
    files = document['files']
    if not isinstance(files, dict) or set(files) != FILES:
        raise ValueError('fixture must contain exactly three database files')
    decoded = {}
    for name, entry in files.items():
        if not isinstance(entry, dict) or set(entry) != {'base64', 'sha256'}:
            raise ValueError('invalid fixture file entry')
        encoded, digest = entry['base64'], entry['sha256']
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE + 2) // 3) or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('invalid fixture encoding or hash')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError('invalid base64 fixture file') from None
        if not data or len(data) > MAX_FILE or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('fixture size or sha256 mismatch')
        if name.endswith('.musicdb'):
            if (len(data) < 160 or data[:4] != b'hfma' or struct.unpack_from('<I', data, 4)[0] != 160
                    or struct.unpack_from('<I', data, 8)[0] != len(data) or struct.unpack_from('<I', data, 128)[0] not in (0, len(data))):
                raise ValueError('unsupported hfma database header')
        elif len(data) < 4096 or len(data) % 4096 or data[16:24] != bytes.fromhex('100001010c402020'):
            raise ValueError('unsupported encrypted Genius page header')
        decoded[name] = data
    tracks = document['expected_tracks']
    if not isinstance(tracks, list) or len(tracks) != 2:
        raise ValueError('expected exactly two synthetic tracks')
    seen = set()
    for index, track in enumerate(tracks, 1):
        if not isinstance(track, dict) or set(track) != {'persistent_id', 'genius_id', 'title', 'duration_ms'}:
            raise ValueError('invalid expected track')
        pid = track['persistent_id']
        if not isinstance(pid, str) or not PID.fullmatch(pid) or pid in seen:
            raise ValueError('invalid or duplicate persistent ID')
        if not isinstance(track['genius_id'], str) or not PID.fullmatch(track['genius_id']) or not 0 <= int(track['genius_id'], 16) <= 0xFFFFFFFF:
            raise ValueError('invalid Genius ID')
        if track['title'] not in ('synthetic-tone-1', 'synthetic-tone-2') or type(track['duration_ms']) is not int or track['duration_ms'] != 3000:
            raise ValueError('only synthetic tone titles are allowed')
        seen.add(pid)
    gids = [int(t['genius_id'], 16) for t in tracks]
    if gids != [0, 0] and (0 in gids or len(set(gids)) != 2):
        raise ValueError('expected both zero or distinct nonzero Genius IDs')
    if {t['title'] for t in tracks} != {'synthetic-tone-1', 'synthetic-tone-2'}:
        raise ValueError('expected two distinct synthetic tone titles')
    return decoded


def decode_fixture_payload(payload):
    """Decode a bounded single gzip stream without printing the supplied payload."""
    if not isinstance(payload, str) or not payload or len(payload) > 60000:
        raise ValueError('invalid fixture payload size')
    try:
        compressed = base64.b64decode(payload, validate=True)
        inflater = zlib.decompressobj(31)
        raw = inflater.decompress(compressed, 350001)
        if len(raw) > 350000 or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise ValueError('invalid or oversized fixture gzip stream')
        document = json.loads(raw)
    except (binascii.Error, zlib.error, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('invalid encoded fixture payload') from None
    validate_fixture(document)
    return document


def query_script():
    return '''with timeout of 70 seconds
set outputText to ""
tell application "Music"
set outputText to "COUNT" & tab & (count of tracks of library playlist 1) & linefeed
repeat with t in tracks of library playlist 1
set outputText to outputText & "TRACK" & tab & (persistent ID of t) & tab & (name of t) & tab & (duration of t as text) & linefeed
end repeat
end tell
return outputText
end timeout'''


def parse_query(stdout):
    lines = stdout.strip().splitlines()
    if not lines or len(lines[0].split('\t')) != 2 or lines[0].split('\t')[0] != 'COUNT':
        raise ValueError('invalid native query count')
    count = int(lines[0].split('\t')[1])
    if not 0 <= count <= 2:
        raise ValueError('native query exceeds synthetic fixture track count')
    tracks = []
    seen = set()
    for line in lines[1:]:
        fields = line.split('\t')
        if len(fields) != 4 or fields[0] != 'TRACK' or not PID.fullmatch(fields[1]) or fields[1] in seen:
            raise ValueError('invalid native query track')
        duration = float(fields[3])
        if not math.isfinite(duration) or not 0 <= duration <= 10:
            raise ValueError('invalid synthetic track duration')
        if fields[2] not in ('synthetic-tone-1', 'synthetic-tone-2'):
            raise ValueError('unexpected non-synthetic native track')
        tracks.append(dict(persistent_id=fields[1], title=fields[2], duration=duration))
        seen.add(fields[1])
    if len(tracks) != count:
        raise ValueError('native query count mismatch')
    return tracks


def menu_script():
    return '''with timeout of 10 seconds
tell application "System Events"
tell process "Music"
set foundItems to {}
try
set libraryItems to menu items of menu 1 of menu item "Library" of menu 1 of menu bar item "File" of menu bar 1
repeat with itemRef in libraryItems
if (name of itemRef as text) contains "Genius" then set end of foundItems to {name of itemRef, enabled of itemRef}
end repeat
end try
return foundItems
end tell
end tell
end timeout'''


def native_query(runner=subprocess.run, popen=subprocess.Popen):
    process = popen(['osascript', '-e', query_script()], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    result = {}
    try:
        result['bootstrap_ui'] = run_step(['osascript', '-e', bootstrap_script()], 30, runner)
        stdout, stderr = process.communicate(timeout=max(1, 75 - (time.monotonic() - started)))
        result['native_query'] = dict(status='ok' if process.returncode == 0 else 'failed', returncode=process.returncode,
                                     stdout=(stdout or '')[:12000], stderr=(stderr or '')[:12000])
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)
        result['native_query'] = {'status': 'timeout'}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    return result


def probe(fixture, output, *, runner=subprocess.run, popen=subprocess.Popen, home=None, explicit_library=False):
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('requires a disposable GitHub Actions macOS runner')
    fixture = Path(fixture).resolve()
    output = Path(output).resolve()
    if fixture.stat().st_size > MAX_DOCUMENT:
        raise ValueError('fixture document too large')
    document = json.loads(fixture.read_text())
    decoded = validate_fixture(document)
    music_directory = (Path(home) if home is not None else Path.home()) / 'Music'
    if music_directory.is_symlink() or (music_directory.exists() and any(p.name.endswith('.musiclibrary') for p in music_directory.rglob('*'))):
        raise ValueError('refusing pre-existing Music library')
    bundle = music_directory / 'Music/Music Library.musiclibrary'
    if any(p.is_symlink() for p in (music_directory, bundle.parent, bundle)):
        raise ValueError('refusing symlink Music destination')
    if output == fixture or output in fixture.parents or music_directory == output or music_directory in output.parents:
        raise ValueError('output overlaps fixture or Music library')
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=1, fixture_kind='synthetic-tone-only', fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
                  app_template_version=document['app_template_version'], explicit_library=explicit_library, platform=platform.platform(), architecture=platform.machine(),
                  probe_network_requests=0, actual_genius_generation_tested=False, steps={}, files={}, expected_tracks=document['expected_tracks'])
    steps = report['steps']
    def step(name, command, timeout=20):
        steps[name] = run_step(command, timeout, runner)
    launched = False
    try:
        bundle.mkdir(parents=True, exist_ok=False)
        (output / 'before').mkdir()
        for name, data in decoded.items():
            (bundle / name).write_bytes(data)
            (output / 'before' / name).write_bytes(data)
            report['files'][name] = {'before_sha256': hashlib.sha256(data).hexdigest(), 'before_size': len(data)}
        # Original fixture import path used by the first Actions probe.
        audio = Path.cwd() / 'data/macos-probe/synthetic-media'
        audio.mkdir(parents=True, exist_ok=True)
        for index, frequency in ((1, 440), (2, 660)):
            target = audio / f'synthetic-tone-{index}.wav'
            if target.exists() or target.is_symlink():
                raise ValueError('refusing pre-existing synthetic audio path')
            make_tone(target, frequency)
        report['synthetic_media_directory'] = str(audio)
        step('os', ['sw_vers'])
        step('music_version', ['/usr/libexec/PlistBuddy', '-c', 'Print :CFBundleShortVersionString', '/System/Applications/Music.app/Contents/Info.plist'])
        launch = ['open', '-a', '/System/Applications/Music.app']
        if explicit_library:
            launch.append(str(bundle))
        step('launch_music', launch)
        launched = True
        step('screenshot_before', ['screencapture', '-x', str(output / 'music-before.png')], 15)
        step('genius_menu_before', ['osascript', '-e', menu_script()], 15)
        steps.update(native_query(runner, popen))
        query = steps['native_query']
        if query['status'] == 'ok':
            try:
                tracks = parse_query(query['stdout'])
                report['observed_tracks'] = tracks
                report['expected_tracks_loaded'] = ({(t['persistent_id'], t['title']) for t in tracks} == {(t['persistent_id'], t['title']) for t in document['expected_tracks']}
                                                    and all(abs(t['duration'] - 3.0) <= .05 for t in tracks))
            except ValueError as error:
                report['query_parse_error'] = str(error)
                report['expected_tracks_loaded'] = False
        else:
            report['expected_tracks_loaded'] = False
        # Welcome and automation prompts can consume the first UI pass; the
        # promotion may appear only after that pass and the native query finish.
        step('bootstrap_ui_after_query', ['osascript', '-e', bootstrap_script()], 30)
        step('genius_menu_after', ['osascript', '-e', menu_script()], 15)
        step('screenshot_after', ['screencapture', '-x', str(output / 'music-after.png')], 15)
    finally:
        if launched:
            step('quit_music', ['osascript', '-e', 'with timeout of 15 seconds\ntell application "Music" to quit\nend timeout'], 20)
            # Wait boundedly for Music to leave before collecting its files.
            for attempt in range(40):
                check = run_step(['pgrep', '-x', 'Music'], 5, runner)
                if check.get('returncode') == 1:
                    report['music_exited'] = True
                    break
                time.sleep(.5)
            else:
                report['music_exited'] = False
                step('screenshot_quit_pending', ['screencapture', '-x', str(output / 'music-quit-pending.png')], 15)
                step('quit_pending_ui', ['osascript', '-e', 'with timeout of 10 seconds\ntell application "System Events" to return name of windows of process "Music"\nend timeout'], 15)
        (output / 'after').mkdir(exist_ok=True)
        for name in FILES:
            target = bundle / name
            if target.is_file() and not target.is_symlink() and target.stat().st_size <= MAX_FILE * 10:
                after = target.read_bytes()
                (output / 'after' / name).write_bytes(after)
                record = report['files'].setdefault(name, {})
                record.update(after_sha256=hashlib.sha256(after).hexdigest(), after_size=len(after))
                record['changed'] = record.get('before_sha256') != record['after_sha256']
        report['support_files'] = []
        support = output / 'support-after'
        support.mkdir(exist_ok=True)
        total = 0
        if bundle.is_dir():
            for target in sorted(bundle.iterdir()):
                if not target.is_file() or target.is_symlink() or target.name in FILES:
                    continue
                size = target.stat().st_size
                info = {'name': target.name, 'size': size, 'copied': False}
                if size <= MAX_FILE * 10 and total + size <= 10 * MAX_FILE * 10:
                    data = target.read_bytes()
                    (support / target.name).write_bytes(data)
                    info.update(copied=True, sha256=hashlib.sha256(data).hexdigest())
                    total += size
                report['support_files'].append(info)
        report['after_copy_consistent_exit'] = report.get('music_exited', False)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--explicit-library', action='store_true')
    args = parser.parse_args()
    try:
        report = probe(args.fixture, args.output, explicit_library=args.explicit_library)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{type(error).__name__}: {error}\n')
    print(json.dumps({'expected_tracks_loaded': report.get('expected_tracks_loaded'), 'actual_genius_generation_tested': False}))


if __name__ == '__main__':
    main()
