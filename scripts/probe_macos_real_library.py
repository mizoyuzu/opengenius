#!/usr/bin/env python3
"""Private Actions-only Music.app probe with a real Library and selected media.

Raw scripts, native metadata, screenshots and database copies are private output.
The console prints only counts and status. No account or Genius opt-in operation.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from probe_macos_music import apple_string, bootstrap_script, dismiss_music_promotion, library_roots
from probe_macos_genius_reload import generate_script

PID = re.compile(r'[0-9A-F]{16}\Z')
CORE = ('Library.musicdb', 'Genius.itdb', 'Library Preferences.musicdb')
MAX_OUTPUT = 512 * 1024


def validate_inputs(directory):
    directory = Path(directory).resolve(strict=True)
    manifest_path = directory / 'manifest.json'
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError('manifest exceeds limit')
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'expected_library_track_count', 'tracks', 'seeds'}:
        raise ValueError('unsupported real Library manifest')
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('unsupported manifest version')
    count = manifest['expected_library_track_count']
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError('invalid expected Library count')
    tracks = manifest['tracks']
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= min(count, 128):
        raise ValueError('selected media track count outside limit')
    seen, media_paths = set(), {}
    total = 0
    for track in tracks:
        if not isinstance(track, dict) or set(track) != {'persistent_id', 'title', 'relative_media_path'}:
            raise ValueError('unsupported media track entry')
        pid, title, relative = (track[k] for k in ('persistent_id', 'title', 'relative_media_path'))
        if not isinstance(pid, str) or not PID.fullmatch(pid) or pid in seen:
            raise ValueError('invalid or duplicate selected PID')
        if not isinstance(title, str) or not title or len(title) > 1024 or any(c in title for c in '\t\r\n\x00'):
            raise ValueError('invalid selected title')
        if not isinstance(relative, str) or not relative or len(relative) > 512 or any(c in relative for c in '\t\r\n\x00'):
            raise ValueError('invalid relative media path')
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or path == Path('.'):
            raise ValueError('media path must stay inside media directory')
        media = directory / 'media' / path
        if media.is_symlink() or any(p.is_symlink() for p in media.parents if p.is_relative_to(directory)):
            raise ValueError('media symlinks are unsupported')
        media = media.resolve(strict=True)
        if not media.is_relative_to(directory / 'media') or not media.is_file():
            raise ValueError('media path outside media directory')
        size = media.stat().st_size
        if not 0 < size <= 512 * 1024 * 1024:
            raise ValueError('selected media file size outside limit')
        total += size
        if total > 2 * 1024 * 1024 * 1024:
            raise ValueError('selected media total size outside limit')
        seen.add(pid)
        media_paths[pid] = media
    seeds = manifest['seeds']
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 8 or any(not isinstance(p, str) or p not in seen for p in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError('seeds must refer to unique selected media PIDs')
    bundle = directory / 'Music Library.musiclibrary'
    if not bundle.is_dir() or bundle.is_symlink():
        raise ValueError('missing real Library bundle')
    for name in CORE:
        if not (bundle / name).is_file():
            raise ValueError('missing core Library file')
    inventory_bundle(bundle)
    return manifest, bundle, media_paths


def inventory_bundle(bundle):
    records = []
    total = 0
    for path in sorted(Path(bundle).rglob('*')):
        if path.is_symlink():
            raise ValueError('Library bundle symlinks are unsupported')
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if size > 32 * 1024 * 1024 or total > 64 * 1024 * 1024 or len(records) >= 2000:
            raise ValueError('Library bundle exceeds copy limit')
        records.append((path, path.relative_to(bundle), size))
    return records


def snapshot_bundle(bundle, output):
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for path, relative, size in inventory_bundle(bundle):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        records.append({'path': str(relative), 'size': size, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
    return records


def private_step(command, timeout=30, runner=subprocess.run):
    """Capture private evidence without printing subprocess output or commands."""
    result = {'command': list(command), 'timeout_seconds': timeout}
    try:
        done = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
        result.update(status='ok' if done.returncode == 0 else 'failed', returncode=done.returncode,
                      stdout=(done.stdout or '')[:MAX_OUTPUT], stderr=(done.stderr or '')[:MAX_OUTPUT],
                      output_truncated=len(done.stdout or '') > MAX_OUTPUT or len(done.stderr or '') > MAX_OUTPUT)
    except subprocess.TimeoutExpired:
        result['status'] = 'timeout'
    except OSError as error:
        result.update(status='unavailable', error_type=type(error).__name__)
    return result


def selected_query_script(tracks):
    lines = ['with timeout of 100 seconds', 'set outputText to ""', 'tell application "Music"',
             'set nativeCount to count of tracks of library playlist 1',
             'if nativeCount > 10000 then error "Library count exceeds probe limit"',
             'set outputText to "LIBRARY_COUNT" & tab & nativeCount & linefeed',
             'if nativeCount is 0 then return outputText',
             'try', 'set allPIDs to persistent ID of every track of library playlist 1',
             'on error', 'set allPIDs to {}', 'repeat with t in tracks of library playlist 1',
             'set end of allPIDs to persistent ID of t', 'end repeat', 'end try',
             'repeat with pid in allPIDs', 'set outputText to outputText & "LIBRARY_PID" & tab & pid & linefeed', 'end repeat']
    for track in tracks:
        pid = track['persistent_id']
        lines += [f'set selectedTracks to every track of library playlist 1 whose persistent ID is "{pid}"',
                  'if (count of selectedTracks) is 1 then', 'set t to item 1 of selectedTracks',
                  'set outputText to outputText & "SELECTED" & tab & (persistent ID of t) & tab & (duration of t as text) & tab & (name of t) & linefeed', 'end if']
    return '\n'.join(lines + ['end tell', 'return outputText', 'end timeout'])


def parse_selected_query(stdout, manifest):
    lines = stdout.strip().splitlines()
    if not lines or len(lines[0].split('\t')) != 2 or lines[0].split('\t')[0] != 'LIBRARY_COUNT':
        raise ValueError('invalid native Library count')
    count = int(lines[0].split('\t')[1])
    if not 0 <= count <= 10000:
        raise ValueError('native Library count outside limit')
    pids, selected = [], {}
    expected = {t['persistent_id']: t for t in manifest['tracks']}
    for line in lines[1:]:
        fields = line.split('\t')
        if len(fields) == 2 and fields[0] == 'LIBRARY_PID' and PID.fullmatch(fields[1]):
            pids.append(fields[1])
        elif len(fields) == 4 and fields[0] == 'SELECTED' and fields[1] in expected and fields[1] not in selected:
            duration = float(fields[2])
            if not math.isfinite(duration) or not 0 <= duration <= 86400:
                raise ValueError('native duration outside limit')
            selected[fields[1]] = {'persistent_id': fields[1], 'duration_seconds': duration, 'title': fields[3],
                                  'title_matches_manifest': fields[3] == expected[fields[1]]['title']}
        else:
            raise ValueError('invalid native Library query row')
    if len(pids) != count or len(set(pids)) != count or any(p not in set(pids) for p in selected):
        raise ValueError('native PID count inconsistent')
    return {'library_count': count, 'library_pids': pids, 'selected_tracks': list(selected.values()),
            'expected_library_count_loaded': count == manifest['expected_library_track_count'],
            'selected_tracks_loaded': set(selected) == set(expected) and all(t['title_matches_manifest'] for t in selected.values())}


def location_writable(sdef):
    root = ET.fromstring(sdef)
    for class_node in root.iter('class'):
        if class_node.get('name') == 'file track':
            for prop in class_node.findall('property'):
                if prop.get('name') == 'location':
                    return prop.get('access', 'rw') in ('rw', 'w')
    return False


def relink_script(media_paths):
    lines = ['with timeout of 150 seconds', 'set outputText to ""', 'tell application "Music"']
    for pid, path in media_paths.items():
        if not PID.fullmatch(pid):
            raise ValueError('invalid relink PID')
        lines += [f'set selectedTracks to every file track of library playlist 1 whose persistent ID is "{pid}"',
                  'if (count of selectedTracks) is 1 then', 'set t to item 1 of selectedTracks', 'try',
                  f'set location of t to (POSIX file {apple_string(path)})',
                  'set linkedLocation to (get location of t)', 'set linkedPath to POSIX path of linkedLocation',
                  'set outputText to outputText & "LINK" & tab & (persistent ID of t) & tab & "OK" & tab & linkedPath & linefeed',
                  'on error', f'set outputText to outputText & "LINK" & tab & "{pid}" & tab & "FAILED" & linefeed', 'end try',
                  'else', f'set outputText to outputText & "LINK" & tab & "{pid}" & tab & "NOT_FILE_TRACK" & linefeed', 'end if']
    return '\n'.join(lines + ['end tell', 'return outputText', 'end timeout'])


def parse_links(stdout, media_paths):
    records = []
    seen = set()
    for line in stdout.strip().splitlines():
        fields = line.split('\t')
        if len(fields) not in (3, 4) or fields[0] != 'LINK' or fields[1] not in media_paths or fields[1] in seen or fields[2] not in ('OK', 'FAILED', 'NOT_FILE_TRACK'):
            raise ValueError('invalid native relink row')
        verified = fields[2] == 'OK' and len(fields) == 4 and Path(fields[3]) == media_paths[fields[1]]
        records.append({'persistent_id': fields[1], 'status': fields[2].lower(), 'location_verified': verified,
                        'observed_path': fields[3] if len(fields) == 4 else None})
        seen.add(fields[1])
    if seen != set(media_paths):
        raise ValueError('native relink rows incomplete')
    return records


def playback_script(pid):
    if not PID.fullmatch(pid):
        raise ValueError('invalid playback seed')
    return f'''with timeout of 20 seconds
tell application "Music"
set t to item 1 of (every track of library playlist 1 whose persistent ID is "{pid}")
play t once true
delay 2
set initialPosition to player position
set initialPID to persistent ID of current track
set initialState to player state as text
delay 2
set laterPosition to player position
set laterPID to persistent ID of current track
set laterState to player state as text
stop
return "PLAYBACK" & tab & initialPID & tab & initialState & tab & initialPosition & tab & laterPID & tab & laterState & tab & laterPosition
end tell
end timeout'''


def parse_playback(stdout, pid):
    fields = stdout.strip().split('\t')
    if len(fields) != 7 or fields[0] != 'PLAYBACK':
        raise ValueError('invalid native playback response')
    first, later = float(fields[3]), float(fields[6])
    if not math.isfinite(first) or not math.isfinite(later) or min(first, later) < 0:
        raise ValueError('invalid native playback position')
    return {'seed_pid': pid, 'initial_pid': fields[1], 'later_pid': fields[4], 'initial_state': fields[2], 'later_state': fields[5],
            'initial_position_seconds': first, 'later_position_seconds': later,
            'position_advanced': fields[1] == fields[4] == pid and fields[2] == fields[5] == 'playing' and later > first + .25,
            'audio_output_recorded': False}


def playlists_script():
    return '''with timeout of 60 seconds
set outputText to ""
tell application "Music"
set geniusLists to every user playlist whose genius is true
if (count of geniusLists) > 32 then error "Genius playlist count exceeds probe limit"
set outputText to "GENIUS_COUNT" & tab & (count of geniusLists) & linefeed
repeat with p in geniusLists
if (count of tracks of p) > 100 then error "Genius playlist size exceeds probe limit"
set outputText to outputText & "GENIUS_PLAYLIST" & tab & (persistent ID of p) & tab & (count of tracks of p) & linefeed
repeat with t in tracks of p
set outputText to outputText & "GENIUS_TRACK" & tab & (persistent ID of p) & tab & (persistent ID of t) & tab & (name of t) & tab & (duration of t as text) & linefeed
end repeat
end repeat
end tell
return outputText
end timeout'''


def parse_playlists(stdout, known_pids):
    lines = stdout.strip().splitlines()
    if not lines or len(lines[0].split('\t')) != 2 or lines[0].split('\t')[0] != 'GENIUS_COUNT':
        raise ValueError('invalid native Genius count')
    count = int(lines[0].split('\t')[1])
    if not 0 <= count <= 32:
        raise ValueError('native Genius count outside limit')
    playlists = {}
    for line in lines[1:]:
        fields = line.split('\t')
        if len(fields) == 3 and fields[0] == 'GENIUS_PLAYLIST' and PID.fullmatch(fields[1]) and fields[1] not in playlists:
            size = int(fields[2])
            if not 0 <= size <= 100:
                raise ValueError('native Genius size outside limit')
            playlists[fields[1]] = {'playlist_pid': fields[1], 'declared_count': size, 'tracks': []}
        elif len(fields) == 5 and fields[0] == 'GENIUS_TRACK' and fields[1] in playlists and fields[2] in known_pids:
            duration = float(fields[4])
            if not math.isfinite(duration) or not 0 <= duration <= 86400:
                raise ValueError('invalid native Genius duration')
            playlists[fields[1]]['tracks'].append({'persistent_id': fields[2], 'title': fields[3], 'duration_seconds': duration})
        else:
            raise ValueError('invalid native Genius row')
    if len(playlists) != count or any(len(p['tracks']) != p['declared_count'] for p in playlists.values()):
        raise ValueError('native Genius counts inconsistent')
    return list(playlists.values())


def trigger_query(script, runner, popen):
    process = popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = {}
    started = time.monotonic()
    try:
        result['bootstrap_ui'] = private_step(['osascript', '-e', bootstrap_script()], 30, runner)
        try:
            stdout, stderr = process.communicate(timeout=max(1, 120 - (time.monotonic() - started)))
            result['native_library_query'] = {'status': 'ok' if process.returncode == 0 else 'failed', 'returncode': process.returncode,
                                              'stdout': (stdout or '')[:MAX_OUTPUT], 'stderr': (stderr or '')[:MAX_OUTPUT],
                                              'output_truncated': len(stdout or '') > MAX_OUTPUT or len(stderr or '') > MAX_OUTPUT}
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            result['native_library_query'] = {'status': 'timeout'}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    return result


def probe(input_directory, output, *, runner=subprocess.run, popen=subprocess.Popen, home=None):
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('requires a disposable Actions macOS runner')
    source = Path(input_directory).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('output must be new and separate from input')
    music = (Path(home) if home else Path.home()) / 'Music'
    if music.is_symlink() or (music.exists() and any(p.name.endswith('.musiclibrary') for p in music.rglob('*'))):
        raise ValueError('refusing pre-existing Music Library')
    manifest, source_bundle, media_paths = validate_inputs(source)
    output.mkdir(parents=True, mode=0o700)
    bundle = music / 'Music/Music Library.musiclibrary'
    if bundle.parent.is_symlink():
        raise ValueError('refusing symlink Music destination')
    bundle.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_bundle, bundle)
    report = {'schema_version': 1, 'private_real_library_probe': True, 'probe_network_requests': 0,
              'music_app_network_activity_observed': False, 'expected_library_track_count': manifest['expected_library_track_count'],
              'restored_bundle_path': str(bundle), 'selected_media_count': len(manifest['tracks']), 'seed_count': len(manifest['seeds']), 'steps': {}, 'seed_results': [],
              'actual_genius_generation_tested': False, 'actual_genius_generation_observed': False}
    steps = report['steps']
    launched = False
    def step(name, command, timeout=30):
        steps[name] = private_step(command, timeout, runner)
        return steps[name]
    def parsed_step(name, command, parser, timeout=30):
        result = step(name, command, timeout)
        if result['status'] == 'ok' and not result.get('output_truncated'):
            try:
                return parser(result['stdout'])
            except (ValueError, OverflowError):
                result['parse_status'] = 'invalid_native_response'
        return None
    try:
        report['before_files'] = snapshot_bundle(bundle, output / 'before')
        step('os', ['sw_vers'])
        step('music_version', ['/usr/libexec/PlistBuddy', '-c', 'Print :CFBundleShortVersionString', '/System/Applications/Music.app/Contents/Info.plist'])
        app_sdef = Path('/System/Applications/Music.app/Contents/Resources/com.apple.Music.sdef')
        report['file_track_location_writable'] = location_writable(app_sdef.read_text()) if app_sdef.is_file() else False
        step('launch_explicit_library', ['open', '-a', '/System/Applications/Music.app', str(bundle)])
        launched = True
        steps.update(trigger_query(selected_query_script(manifest['tracks']), runner, popen))
        query = steps['native_library_query']
        native = None
        if query['status'] == 'ok' and not query.get('output_truncated'):
            try:
                native = parse_selected_query(query['stdout'], manifest)
                report['native_library'] = native
            except (ValueError, OverflowError):
                query['parse_status'] = 'invalid_native_response'
        steps['bootstrap_ui_after_query'] = private_step(['osascript', '-e', bootstrap_script()], 30, runner)
        steps['dismiss_music_promotion'] = dismiss_music_promotion(output, runner)
        if not native or not native['expected_library_count_loaded'] or not native['selected_tracks_loaded']:
            # The first AppleEvent may have been blocked by a first-launch modal.
            # Retry once after the bounded welcome and promotion dismissal.
            retry = parsed_step('native_library_query_after_dismissal', ['osascript', '-e', selected_query_script(manifest['tracks'])],
                                lambda text: parse_selected_query(text, manifest), 110)
            if retry is not None:
                native = retry
                report['native_library'] = native
        step('screenshot_loaded', ['screencapture', '-x', str(output / 'music-loaded.png')], 15)
        if not native or not native['expected_library_count_loaded'] or not native['selected_tracks_loaded']:
            report['status'] = 'library_load_not_verified'
        else:
            report['status'] = 'library_loaded'
            if report['file_track_location_writable']:
                report['relink_results'] = parsed_step('relink_selected_media', ['osascript', '-e', relink_script(media_paths)], lambda text: parse_links(text, media_paths), 170)
            else:
                report['relink_status'] = 'file_track_location_not_writable'
            known_pids = set(native['library_pids'])
            baseline = parsed_step('genius_playlists_before', ['osascript', '-e', playlists_script()], lambda text: parse_playlists(text, known_pids), 70)
            existing = {p['playlist_pid'] for p in baseline or []}
            linked = {r['persistent_id'] for r in report.get('relink_results') or [] if r['location_verified']}
            for index, pid in enumerate(manifest['seeds'], 1):
                seed = {'persistent_id': pid, 'media_location_verified': pid in linked}
                if pid in linked:
                    seed['playback'] = parsed_step(f'playback_{index}', ['osascript', '-e', playback_script(pid)], lambda text, pid=pid: parse_playback(text, pid), 25)
                else:
                    seed['playback_status'] = 'skipped_media_location_not_verified'
                action = step(f'genius_menu_action_{index}', ['osascript', '-e', generate_script(pid)], 20)
                menu_status = action.get('stdout', '').strip() if action['status'] == 'ok' else action['status']
                if menu_status not in ('CLICKED', 'DISABLED', 'SEED_MISSING', 'UNAVAILABLE', 'failed', 'timeout', 'unavailable'):
                    menu_status = 'unexpected_native_response'
                seed['genius_menu_status'] = menu_status.lower()
                seed['generation_attempted'] = menu_status == 'CLICKED'
                report['actual_genius_generation_tested'] |= seed['generation_attempted']
                if seed['generation_attempted']:
                    time.sleep(2)
                    playlists = parsed_step(f'genius_playlists_after_{index}', ['osascript', '-e', playlists_script()], lambda text: parse_playlists(text, known_pids), 70)
                    seed['observed_genius_playlists'] = playlists
                    seed['new_genius_playlists'] = [p for p in playlists or [] if p['playlist_pid'] not in existing] if baseline is not None else None
                    seed['generation_observed'] = bool(seed['new_genius_playlists']) and any(p['tracks'] for p in seed['new_genius_playlists'])
                    report['actual_genius_generation_observed'] |= seed['generation_observed']
                    existing.update(p['playlist_pid'] for p in playlists or [])
                step(f'screenshot_seed_{index}', ['screencapture', '-x', str(output / f'music-seed-{index}.png')], 15)
                report['seed_results'].append(seed)
    finally:
        if launched:
            step('stop_playback', ['osascript', '-e', 'with timeout of 5 seconds\ntell application "Music" to stop\nend timeout'], 10)
            step('quit_music', ['osascript', '-e', 'with timeout of 15 seconds\ntell application "Music" to quit\nend timeout'], 20)
            report['music_exited'] = False
            for _ in range(40):
                check = private_step(['pgrep', '-x', 'Music'], 5, runner)
                if check.get('returncode') == 1:
                    report['music_exited'] = True
                    break
                time.sleep(.5)
            if not report['music_exited']:
                step('screenshot_quit_pending', ['screencapture', '-x', str(output / 'music-quit-pending.png')], 15)
        report['after_copy_consistent_exit'] = report.get('music_exited', False)
        report['music_library_locations'] = []
        for index, other in enumerate(library_roots(music)):
            info = {'path': str(other), 'is_restored_bundle': other == bundle}
            if other != bundle:
                try:
                    info['files'] = snapshot_bundle(other, output / 'other-libraries' / str(index))
                except (ValueError, OSError):
                    info['copy_status'] = 'failed_or_exceeded_limit'
            report['music_library_locations'].append(info)
        step('music_defaults', ['defaults', 'read', 'com.apple.Music'], 15)
        try:
            report['after_files'] = snapshot_bundle(bundle, output / 'after')
        except (ValueError, OSError):
            report['after_copy_status'] = 'failed_or_exceeded_limit'
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-directory', '--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = probe(args.input_directory, args.output)
    except (ValueError, OSError, ET.ParseError):
        parser.exit(1, 'Private native probe could not complete; inspect encrypted diagnostics.\n')
    print(json.dumps({'status': report.get('status'), 'selected_media_count': report['selected_media_count'],
                      'generation_attempted': report['actual_genius_generation_tested'],
                      'generation_observed': report['actual_genius_generation_observed']}))


if __name__ == '__main__':
    main()
