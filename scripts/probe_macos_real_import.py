#!/usr/bin/env python3
"""Explicitly import selected real media into a fresh native Music Library.

Private native output establishes original PID -> newly imported PID mapping.
No supplied bundle is restored and no replacement Genius relations are installed.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time

from probe_macos_music import apple_string, bootstrap_script, dismiss_music_promotion, library_roots
from probe_macos_genius_reload import menu_script
from probe_macos_real_library import (MAX_OUTPUT, PID, parse_playback, parse_selected_query,
                                      playback_script, private_step, selected_query_script,
                                      snapshot_bundle, validate_inputs)


def real_import_script(media_paths):
    lines = [
        'global probeStage', 'set probeStage to "initializing"',
        'on sanitizedDiagnostic(rawText)',
        'set cleanText to rawText as text',
        "set savedDelimiters to AppleScript's text item delimiters",
        'repeat with separator in {tab, linefeed, return}',
        "set AppleScript's text item delimiters to separator as text",
        'set pieces to text items of cleanText',
        "set AppleScript's text item delimiters to \" \"",
        'set cleanText to pieces as text', 'end repeat',
        "set AppleScript's text item delimiters to savedDelimiters",
        'if (length of cleanText) > 2048 then set cleanText to text 1 thru 2048 of cleanText',
        'return cleanText', 'end sanitizedDiagnostic',
        'on currentLibraryPIDs()', 'global probeStage', 'set callingStage to probeStage',
        'tell application "Music"', 'set resultPIDs to {}',
        'set probeStage to callingStage & ":count"',
        'set trackCount to count of tracks of library playlist 1',
        'if trackCount is 0 then return resultPIDs',
        'repeat with i from 1 to trackCount',
        'set probeStage to callingStage & ":getPID"',
        'set retrievedPID to (get persistent ID of track i of library playlist 1)',
        'set probeStage to callingStage & ":coercePID"',
        'set textPID to retrievedPID as text', 'set end of resultPIDs to textPID',
        'end repeat', 'return resultPIDs', 'end tell', 'end currentLibraryPIDs',
        'with timeout of 180 seconds', 'set outputText to ""', 'tell application "Music"',
    ]
    for pid, path in media_paths.items():
        if not isinstance(pid, str) or not PID.fullmatch(pid):
            raise ValueError('invalid original PID')
        lines += ['try', 'set probeStage to "beforePIDs"', 'set beforePIDs to my currentLibraryPIDs()',
                  'set probeStage to "add"', f'add {{(POSIX file {apple_string(path)})}}',
                  'set probeStage to "afterPIDs"', 'set afterPIDs to my currentLibraryPIDs()',
                  'set probeStage to "resolving"', 'set newPIDs to {}',
                  'repeat with candidatePID in afterPIDs', 'set candidateText to candidatePID as text',
                  'if beforePIDs does not contain candidateText then set end of newPIDs to candidateText',
                  'end repeat', 'if (count of newPIDs) is not 1 then error "Unexpected native PID difference" number -2700',
                  'set nativePID to item 1 of newPIDs',
                  'set t to item 1 of (every track of library playlist 1 whose persistent ID is nativePID)',
                  'set probeStage to "getduration"', 'set nativeDuration to (get duration of t)',
                  'set durationText to nativeDuration as text',
                  'set probeStage to "getname"', 'set nativeTitle to (get name of t)',
                  'set probeStage to "getlocation"', 'set nativeLocation to (get location of t)',
                  'set probeStage to "coerceLocation"', 'set nativePath to POSIX path of nativeLocation',
                  'set probeStage to "formatting"',
                  f'set outputText to outputText & "IMPORTED" & tab & "{pid}" & tab & nativePID & tab & durationText & tab & nativeTitle & tab & nativePath & linefeed',
                  'on error errorText number errorNumber',
                  f'set outputText to outputText & "IMPORT_FAILED" & tab & "{pid}" & tab & (errorNumber as text) & tab & probeStage & tab & (my sanitizedDiagnostic(errorText)) & linefeed', 'end try']
    return '\n'.join(lines + ['set nativeCount to count of tracks of library playlist 1', 'end tell',
                               'return "IMPORT_LIBRARY_COUNT" & tab & nativeCount & linefeed & outputText', 'end timeout'])


def parse_import(stdout, manifest):
    lines = stdout.strip().splitlines()
    fields = lines[0].split('\t') if lines else []
    if len(fields) != 2 or fields[0] != 'IMPORT_LIBRARY_COUNT':
        raise ValueError('invalid native import count')
    count = int(fields[1])
    if not 0 <= count <= 128:
        raise ValueError('native import count exceeds selected media limit')
    originals = {t['persistent_id']: t for t in manifest['tracks']}
    seen, native_seen, records = set(), set(), []
    for line in lines[1:]:
        fields = line.split('\t')
        if len(fields) not in (2, 3, 5, 6) or fields[1] not in originals or fields[1] in seen:
            raise ValueError('invalid or duplicate original import PID')
        seen.add(fields[1])
        if len(fields) in (2, 3, 5) and fields[0] == 'IMPORT_FAILED':
            records.append({'original_pid': fields[1], 'status': 'failed', 'native_error_number': int(fields[2]) if len(fields) >= 3 else None,
                            'native_error_stage': fields[3] if len(fields) == 5 else None, 'native_error_text': fields[4] if len(fields) == 5 else None})
            continue
        if len(fields) != 6 or fields[0] != 'IMPORTED' or not PID.fullmatch(fields[2]) or fields[2] in native_seen:
            raise ValueError('invalid or duplicate native import PID')
        duration = float(fields[3])
        if not math.isfinite(duration) or not 0 <= duration <= 86400 or not fields[4] or not Path(fields[5]).is_absolute():
            raise ValueError('invalid native imported metadata')
        native_seen.add(fields[2])
        records.append({'original_pid': fields[1], 'persistent_id': fields[2], 'duration_seconds': duration,
                        'duration_ms': round(duration * 1000), 'relative_media_path': originals[fields[1]]['relative_media_path'],
                        'title': fields[4], 'native_location': fields[5], 'status': 'imported'})
    if seen != set(originals) or count < len(native_seen):
        raise ValueError('native import rows and Library count inconsistent')
    return {'native_library_count': count, 'tracks': records, 'all_selected_media_imported': len(native_seen) == len(originals) == count, 'native_tracks_without_mapping': count - len(native_seen)}


def import_with_bootstrap(media_paths, runner, popen):
    process = popen(['osascript', '-e', real_import_script(media_paths)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = {}
    started = time.monotonic()
    try:
        result['bootstrap_ui'] = private_step(['osascript', '-e', bootstrap_script()], 30, runner)
        try:
            stdout, stderr = process.communicate(timeout=max(1, 210 - (time.monotonic() - started)))
            result['native_import'] = {'status': 'ok' if process.returncode == 0 else 'failed', 'returncode': process.returncode,
                                       'stdout': (stdout or '')[:MAX_OUTPUT], 'stderr': (stderr or '')[:MAX_OUTPUT],
                                       'output_truncated': len(stdout or '') > MAX_OUTPUT or len(stderr or '') > MAX_OUTPUT}
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            result['native_import'] = {'status': 'timeout'}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    return result


def probe(input_directory, output, *, runner=subprocess.run, popen=subprocess.Popen, home=None):
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('requires a disposable Actions macOS runner')
    source, output = Path(input_directory).resolve(strict=True), Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('output must be new and separate from input')
    music = (Path(home) if home else Path.home()) / 'Music'
    if music.is_symlink() or (music.exists() and any(p.name.endswith('.musiclibrary') for p in music.rglob('*'))):
        raise ValueError('refusing pre-existing Music Library')
    manifest, _, media_paths = validate_inputs(source)
    output.mkdir(parents=True, mode=0o700)
    report = {'schema_version': 1, 'private_real_media_import': True, 'supplied_bundle_restored': False,
              'replacement_genius_relations_installed': False, 'probe_network_requests': 0,
              'actual_genius_generation_tested': False, 'selected_media_count': len(manifest['tracks']),
              'steps': {}, 'seed_results': []}
    steps = report['steps']
    launched = False
    def step(name, command, timeout=30):
        steps[name] = private_step(command, timeout, runner)
        return steps[name]
    try:
        step('os', ['sw_vers'])
        step('music_version', ['/usr/libexec/PlistBuddy', '-c', 'Print :CFBundleShortVersionString', '/System/Applications/Music.app/Contents/Info.plist'])
        step('launch_fresh_music', ['open', '-a', '/System/Applications/Music.app'])
        launched = True
        steps.update(import_with_bootstrap(media_paths, runner, popen))
        parsed = None
        native = steps['native_import']
        if native['status'] == 'ok' and not native.get('output_truncated'):
            try:
                parsed = parse_import(native['stdout'], manifest)
                report['native_import_mapping'] = parsed
                (output / 'native-mapping.json').write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + '\n')
            except (ValueError, OverflowError):
                native['parse_status'] = 'invalid_native_response'
        steps['bootstrap_ui_after_import'] = private_step(['osascript', '-e', bootstrap_script()], 30, runner)
        steps['dismiss_music_promotion'] = dismiss_music_promotion(output, runner)
        step('screenshot_imported', ['screencapture', '-x', str(output / 'music-imported.png')], 15)
        report['status'] = 'native_import_verified' if parsed and parsed['all_selected_media_imported'] else 'native_import_not_verified'
        if parsed:
            imported = {t['original_pid']: t for t in parsed['tracks'] if t['status'] == 'imported'}
            native_manifest = {'expected_library_track_count': len(imported), 'tracks': list(imported.values())}
            query = step('native_imported_library_query', ['osascript', '-e', selected_query_script(native_manifest['tracks'])], 110)
            if query['status'] == 'ok' and not query.get('output_truncated'):
                try:
                    report['native_library'] = parse_selected_query(query['stdout'], native_manifest)
                except (ValueError, OverflowError):
                    query['parse_status'] = 'invalid_native_response'
            for index, original_pid in enumerate(manifest['seeds'], 1):
                mapped = imported.get(original_pid)
                result = {'original_pid': original_pid}
                if mapped:
                    pid = mapped['persistent_id']
                    result['persistent_id'] = pid
                    playback = step(f'playback_{index}', ['osascript', '-e', playback_script(pid)], 25)
                    if playback['status'] == 'ok' and not playback.get('output_truncated'):
                        try:
                            result['playback'] = parse_playback(playback['stdout'], pid)
                        except (ValueError, OverflowError):
                            playback['parse_status'] = 'invalid_native_response'
                    # Reveal the exact new native seed, then inventory menus only.
                    # Fresh native files carry no installed replacement relation data.
                    reveal = 'with timeout of 10 seconds\ntell application "Music"\nactivate\nreveal item 1 of (every track of library playlist 1 whose persistent ID is "' + pid + '")\nend tell\nend timeout'
                    step(f'reveal_seed_{index}', ['osascript', '-e', reveal], 15)
                    step(f'genius_menu_{index}', ['osascript', '-e', menu_script()], 15)
                    step(f'screenshot_seed_{index}', ['screencapture', '-x', str(output / f'music-seed-{index}.png')], 15)
                else:
                    result['status'] = 'seed_import_failed'
                report['seed_results'].append(result)
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
        roots = library_roots(music)
        report['native_library_locations'] = [str(p) for p in roots]
        preferred = music / 'Music/Music Library.musiclibrary'
        primary = preferred if preferred in roots else roots[0] if len(roots) == 1 else None
        if primary:
            try:
                report['after_files'] = snapshot_bundle(primary, output / 'after')
            except (ValueError, OSError):
                report['after_copy_status'] = 'failed_or_exceeded_limit'
        else:
            report['after_copy_status'] = 'native_bundle_not_uniquely_identified'
        step('music_defaults', ['defaults', 'read', 'com.apple.Music'], 15)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-directory', '--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = probe(args.input_directory, args.output)
    except (ValueError, OSError):
        parser.exit(1, 'Private native import did not complete; inspect encrypted diagnostics.\n')
    print(json.dumps({'status': report['status'], 'selected_media_count': report['selected_media_count'], 'replacement_genius_relations_installed': False}))


if __name__ == '__main__':
    main()
